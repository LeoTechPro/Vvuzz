package monitor

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var apiPattern = regexp.MustCompile(`^/api/university-applicant-list/v1/public/(20[0-9]{2})/competition/([1-9][0-9]{0,12})/(applicants|ratings)$`)
var pagePattern = regexp.MustCompile(`/(applicants|ratings)/([1-9][0-9]{0,12})/?$`)

func sourceURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "https" || u.Host != "www.gosuslugi.ru" || u.User != nil || u.Fragment != "" {
		return "", errors.New("нужна публичная HTTPS-ссылка www.gosuslugi.ru без авторизации")
	}
	if apiPattern.MatchString(u.Path) && u.RawQuery == "" {
		return u.String(), nil
	}
	if strings.HasPrefix(u.Path, "/vuznavigator/specialties/") {
		if m := pagePattern.FindStringSubmatch(u.Path); m != nil {
			// shortcut: navigator URLs do not encode the campaign year, use 2026 until a new campaign adapter is verified.
			return "https://www.gosuslugi.ru/api/university-applicant-list/v1/public/2026/competition/" + m[2] + "/" + m[1], nil
		}
	}
	return "", errors.New("ссылка должна вести на конкретный список applicants или ratings")
}

func (e *Engine) Run(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		case <-e.wake:
		}
		if e.Paused() || time.Now().Before(e.SourceCooldown()) {
			continue
		}
		watches, err := e.Watches(ctx)
		if err != nil {
			continue
		}
		for _, w := range watches {
			if ctx.Err() != nil {
				return
			}
			if e.Paused() || time.Now().Before(e.SourceCooldown()) {
				break
			}
			next, _ := time.Parse(time.RFC3339, w.Next)
			if time.Now().Before(next) {
				continue
			}
			e.Update(ctx, w)
			// One global worker, at most five requests/minute, with no catch-up burst after sleep.
			timer := time.NewTimer(12 * time.Second)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}
}

func (e *Engine) Update(ctx context.Context, w Watch) {
	if time.Now().Before(e.SourceCooldown()) {
		return
	}
	now := time.Now().UTC()
	stamp := now.Format(time.RFC3339)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, w.URL, nil)
	if err != nil {
		e.failed(ctx, w, err, 0)
		return
	}
	req.Header.Set("User-Agent", "Vvuzz/0.0.1 (+https://github.com/LeoTechPro/Vvuzz; local selected-list monitor)")
	req.Header.Set("Accept", "application/json")
	if w.ETag != "" {
		req.Header.Set("If-None-Match", w.ETag)
	}
	if w.Modified != "" {
		req.Header.Set("If-Modified-Since", w.Modified)
	}
	response, err := e.client.Do(req)
	if err != nil {
		e.failed(ctx, w, errors.New("источник недоступен: сеть или таймаут"), 0)
		return
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotModified {
		if w.Updated == "" {
			e.failed(ctx, w, errors.New("источник вернул 304 до первого снимка"), 0)
			return
		}
		_, _ = e.db.ExecContext(ctx, "UPDATE watches SET checked=?,next=?,error='',failures=0 WHERE id=?", stamp, now.Add(30*time.Minute).Format(time.RFC3339), w.ID)
		return
	}
	if response.StatusCode != http.StatusOK {
		delay := time.Duration(0)
		if seconds, err := strconv.ParseInt(response.Header.Get("Retry-After"), 10, 64); err == nil && seconds > 0 {
			if seconds > int64((365*24*time.Hour)/time.Second) {
				seconds = int64((365 * 24 * time.Hour) / time.Second)
			}
			delay = time.Duration(seconds) * time.Second
		} else if at, err := http.ParseTime(response.Header.Get("Retry-After")); err == nil {
			delay = time.Until(at)
		}
		if response.StatusCode == 401 || response.StatusCode == 403 || response.StatusCode == 429 {
			if delay < 3*time.Hour {
				delay = 3 * time.Hour
			}
		}
		if delay > 0 {
			_, _ = e.db.ExecContext(ctx, "INSERT INTO settings(key,value) VALUES('source_cooldown',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", time.Now().UTC().Add(delay).Format(time.RFC3339))
		}
		e.failed(ctx, w, fmt.Errorf("источник ответил HTTP %d; повтор отложен", response.StatusCode), delay)
		return
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
	if err != nil || len(payload) > 16<<20 {
		e.failed(ctx, w, errors.New("ответ повреждён или превышает 16 МиБ"), 0)
		return
	}
	entries, err := parseEntries(payload)
	if err != nil {
		e.failed(ctx, w, err, 0)
		return
	}
	list := w.List
	list.Entries = entries
	encoded, err := json.Marshal(list)
	if err != nil {
		e.failed(ctx, w, err, 0)
		return
	}
	previous, _ := json.Marshal(w.List)
	tx, err := e.db.BeginTx(ctx, nil)
	if err != nil {
		return
	}
	defer tx.Rollback()
	_, err = tx.ExecContext(ctx, "UPDATE watches SET payload=?,updated=?,checked=?,next=?,error='',failures=0,etag=?,modified=? WHERE id=?", string(encoded), stamp, stamp, now.Add(30*time.Minute).Format(time.RFC3339), response.Header.Get("ETag"), response.Header.Get("Last-Modified"), w.ID)
	if err != nil {
		return
	}
	if sha256.Sum256(previous) != sha256.Sum256(encoded) {
		rank, above := Rank(list, w.Applicant)
		if _, err = tx.ExecContext(ctx, "INSERT INTO changes(watch_id,at,rank,above_consent,total) VALUES(?,?,?,?,?)", w.ID, stamp, rank, above, len(entries)); err != nil {
			return
		}
		// Keep at most 2,000 changed observations per selected list; latest list is kept separately.
		if _, err = tx.ExecContext(ctx, "DELETE FROM changes WHERE watch_id=? AND id NOT IN (SELECT id FROM changes WHERE watch_id=? ORDER BY id DESC LIMIT 2000)", w.ID, w.ID); err != nil {
			return
		}
	}
	_ = tx.Commit()
}

func (e *Engine) SourceCooldown() time.Time {
	var value string
	_ = e.db.QueryRow("SELECT value FROM settings WHERE key='source_cooldown'").Scan(&value)
	at, _ := time.Parse(time.RFC3339, value)
	return at
}

func (e *Engine) failed(ctx context.Context, w Watch, err error, minimum time.Duration) {
	failures := w.Failures + 1
	if failures > 10 {
		failures = 10
	}
	delay := time.Minute * time.Duration(1<<failures)
	if delay > 24*time.Hour {
		delay = 24 * time.Hour
	}
	if delay < minimum {
		delay = minimum
	}
	if delay > 7*24*time.Hour {
		delay = 7 * 24 * time.Hour
	}
	_, _ = e.db.ExecContext(ctx, "UPDATE watches SET error=?,failures=?,checked=?,next=? WHERE id=?", err.Error(), failures, time.Now().UTC().Format(time.RFC3339), time.Now().UTC().Add(delay).Format(time.RFC3339), w.ID)
}

func parseEntries(payload []byte) ([]Entry, error) {
	var raw json.RawMessage
	if err := json.Unmarshal(payload, &raw); err != nil {
		return nil, errors.New("источник вернул не JSON; прошлый список сохранён")
	}
	var rows []json.RawMessage
	if len(raw) > 0 && raw[0] == '[' {
		if err := json.Unmarshal(raw, &rows); err != nil {
			return nil, err
		}
	} else {
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(raw, &obj); err != nil {
			return nil, errors.New("неизвестная схема списка")
		}
		if value, ok := obj["errorCode"]; ok && string(value) != "null" && string(value) != "0" && string(value) != `""` {
			return nil, errors.New("API вернул ошибку; прошлый список сохранён")
		}
		found := false
		for _, key := range []string{"applicants", "applications", "items", "list"} {
			if value, ok := obj[key]; ok && len(value) > 0 && value[0] == '[' {
				if err := json.Unmarshal(value, &rows); err != nil {
					return nil, err
				}
				found = true
				break
			}
		}
		if !found {
			return nil, errors.New("схема источника изменилась; прошлый список сохранён")
		}
	}
	if len(rows) > 100000 {
		return nil, errors.New("слишком большой список")
	}
	entries := make([]Entry, 0, len(rows))
	seen := make(map[string]bool, len(rows))
	for _, rawRow := range rows {
		var row map[string]json.RawMessage
		if err := json.Unmarshal(rawRow, &row); err != nil {
			return nil, errors.New("некорректная строка списка")
		}
		text := func(keys ...string) string {
			for _, key := range keys {
				if v, ok := row[key]; ok && string(v) != "null" {
					var s string
					if json.Unmarshal(v, &s) == nil {
						return s
					}
					return string(v)
				}
			}
			return ""
		}
		id := text("idApplication", "applicantId", "applicationId")
		if !validID(id) || seen[id] {
			return nil, errors.New("нет уникального ID в строках; прошлый список сохранён")
		}
		seen[id] = true
		integer := func(keys ...string) (int, error) {
			value := text(keys...)
			if value == "" {
				return 0, nil
			}
			return strconv.Atoi(value)
		}
		rank, rankErr := integer("rating", "placeInRating", "place")
		priority, priorityErr := integer("priority")
		score := float64(0)
		var scoreErr error
		if value := text("sumMark", "totalScore"); value != "" {
			score, scoreErr = strconv.ParseFloat(value, 64)
		}
		if rankErr != nil || priorityErr != nil || scoreErr != nil || math.IsNaN(score) || math.IsInf(score, 0) {
			return nil, errors.New("некорректное числовое поле; прошлый список сохранён")
		}
		consent := strings.ToLower(strings.TrimSpace(text("consent")))
		var hasConsent bool
		switch consent {
		case "true", "1", "да":
			hasConsent = true
		case "", "false", "0", "нет", "—", "-":
		default:
			return nil, errors.New("неизвестный формат согласия; прошлый список сохранён")
		}
		if rank < 0 || rank > 100000 || priority < 0 || score < 0 || score > 10000 {
			return nil, errors.New("некорректные значения списка")
		}
		entries = append(entries, Entry{ID: id, Rank: rank, Priority: priority, Score: score, Consent: hasConsent})
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].Rank == entries[j].Rank {
			return entries[i].ID < entries[j].ID
		}
		return entries[i].Rank < entries[j].Rank
	})
	return entries, nil
}
