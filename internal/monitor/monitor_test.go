package monitor

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type transport func(*http.Request) (*http.Response, error)

func (f transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestMonitorRetainsGoodSnapshotAndPersistsBackoff(t *testing.T) {
	dir := t.TempDir()
	ctx := context.Background()
	e, err := Open(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	if err = e.AddWatch(ctx, "700002", "https://www.gosuslugi.ru/api/university-applicant-list/v1/public/2026/competition/123/ratings", "Тестовый конкурс", 10); err != nil {
		t.Fatal(err)
	}
	respond := func(status int, body string, headers http.Header) {
		e.client.Transport = transport(func(r *http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
		})
	}
	respond(200, `{"applicants":[{"idApplication":"700001","rating":1,"consent":true,"sumMark":280},{"idApplication":"700002","rating":2,"consent":false,"sumMark":275}]}`, http.Header{"Etag": []string{`"first"`}})
	watches, _ := e.Watches(ctx)
	e.Update(ctx, watches[0])
	watches, _ = e.Watches(ctx)
	rank, above := Rank(watches[0].List, "700002")
	if rank != 2 || above != 1 || watches[0].Updated == "" {
		t.Fatalf("bad snapshot: rank=%d above=%d", rank, above)
	}
	respond(200, `{"error":"season closed"}`, nil)
	e.Update(ctx, watches[0])
	watches, _ = e.Watches(ctx)
	if len(watches[0].List.Entries) != 2 || watches[0].Error == "" {
		t.Fatal("schema error replaced good data")
	}
	respond(429, `blocked`, http.Header{"Retry-After": []string{"14400"}})
	e.Update(ctx, watches[0])
	watches, _ = e.Watches(ctx)
	next, _ := time.Parse(time.RFC3339, watches[0].Next)
	if time.Until(next) < 239*time.Minute {
		t.Fatal("Retry-After not respected")
	}
	if err = e.SetPaused(true); err != nil {
		t.Fatal(err)
	}
	if _, err = e.db.Exec("INSERT INTO settings(key,value) VALUES('archive',?)", filepath.Join(dir, "missing.sqlite")); err != nil {
		t.Fatal(err)
	}
	e.Close()
	e, err = Open(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	watches, err = e.Watches(ctx)
	if err != nil || !e.Paused() || len(watches) != 1 || len(watches[0].List.Entries) != 2 || watches[0].Failures != 2 {
		t.Fatal("restart lost monitor state")
	}
	if e.archiveError == "" || time.Until(e.SourceCooldown()) < 239*time.Minute {
		t.Fatal("optional archive failure or source cooldown lost on restart")
	}
	calls := 0
	e.client.Transport = transport(func(*http.Request) (*http.Response, error) { calls++; return nil, nil })
	e.Update(ctx, watches[0])
	if calls != 0 {
		t.Fatal("global cooldown allowed a request")
	}
	var changes int
	if err = e.db.QueryRow("SELECT COUNT(*) FROM changes").Scan(&changes); err != nil || changes != 1 {
		t.Fatal("errors must not create observations")
	}
}

func TestArchiveReadOnlyAndBrowserBoundary(t *testing.T) {
	dir := t.TempDir()
	archive := filepath.Join(dir, "historical.sqlite")
	db, err := sql.Open("sqlite", archive)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`CREATE TABLE competitions(id INTEGER PRIMARY KEY,program TEXT,university TEXT,quota_type TEXT,seats INTEGER,imported_at TEXT);CREATE TABLE entries(competition_id INTEGER,applicant_id TEXT,ordinal INTEGER,has_consent INTEGER);INSERT INTO competitions VALUES(1,'Тестовая программа','Тестовый вуз','Основные',12,'2025-07-24T10:00:00Z');INSERT INTO entries VALUES(1,'700001',1,1),(1,'700002',2,0);`)
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	before, err := os.ReadFile(archive)
	if err != nil {
		t.Fatal(err)
	}
	e, err := Open(filepath.Join(t.TempDir(), ""), archive)
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	result, err := e.Lookup(context.Background(), "700002")
	if err != nil || len(result) != 1 || result[0].AboveConsent != 1 || result[0].Rank != 2 {
		t.Fatalf("lookup failed: %v %+v", err, result)
	}
	if _, err = e.archive.Exec("DELETE FROM entries"); err == nil {
		t.Fatal("archive is writable")
	}
	after, _ := os.ReadFile(archive)
	if string(before) != string(after) {
		t.Fatal("archive bytes changed")
	}
	handler := e.Handler("127.0.0.1:9177")
	for _, tc := range []struct {
		host, origin, local string
		want                int
	}{{"attacker.test:9177", "", "1", 403}, {"127.0.0.1:9177", "https://evil.test", "1", 403}, {"127.0.0.1:9177", "", "", 403}, {"127.0.0.1:9177", "http://127.0.0.1:9177", "1", 200}} {
		r := httptest.NewRequest("POST", "http://127.0.0.1:9177/api/pause", strings.NewReader(`{"paused":true}`))
		r.Host = tc.host
		r.Header.Set("Origin", tc.origin)
		r.Header.Set("X-Vvuzz-Local", tc.local)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatalf("boundary returned %d want %d", w.Code, tc.want)
		}
	}
	r := httptest.NewRequest("GET", "http://127.0.0.1:9177/api/state", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	var state map[string]json.RawMessage
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &state) != nil {
		t.Fatal("invalid state")
	}
	for _, source := range []string{"http://www.gosuslugi.ru/api/a", "https://www.gosuslugi.ru.evil.test/vuznavigator/specialties/a/ratings/1", "https://user:pass@www.gosuslugi.ru/api/university-applicant-list/v1/public/2026/competition/1/ratings", "https://127.0.0.1/ratings/1"} {
		if _, err = sourceURL(source); err == nil {
			t.Fatal("unsafe source accepted")
		}
	}
}

func TestParserRejectsAmbiguousIdentifiersAndConsent(t *testing.T) {
	for _, payload := range []string{`<html>captcha</html>`, `{"ok":true}`, `[{"idApplication":"700001","consent":"unknown"}]`, `[{"idApplication":"700001"},{"idApplication":"700001"}]`, `[{"name":"private person"}]`, `[{"idApplication":"700001","rating":"unknown"}]`, `[{"idApplication":"700001","sumMark":"NaN"}]`, `[{"idApplication":"700001","priority":"first"}]`} {
		if _, err := parseEntries([]byte(payload)); err == nil {
			t.Fatalf("invalid source accepted: %s", payload)
		}
	}
	entries, err := parseEntries([]byte(`{"items":[]}`))
	if err != nil || len(entries) != 0 {
		t.Fatal("explicit empty list should be valid")
	}
}
