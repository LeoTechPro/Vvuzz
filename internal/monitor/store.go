package monitor

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

type Entry struct {
	ID       string  `json:"applicant_id"`
	Rank     int     `json:"ordinal"`
	Priority int     `json:"priority"`
	Consent  bool    `json:"has_consent"`
	Score    float64 `json:"score"`
}

type List struct {
	Title      string  `json:"title"`
	University string  `json:"university"`
	Seats      int     `json:"seats"`
	Entries    []Entry `json:"entries"`
}

type Watch struct {
	ID           int64  `json:"id"`
	Applicant    string `json:"applicant_id"`
	URL          string `json:"url"`
	List         List   `json:"list"`
	Updated      string `json:"updated_at"`
	Checked      string `json:"checked_at"`
	Next         string `json:"next_at"`
	Error        string `json:"error"`
	Failures     int    `json:"failures"`
	ETag         string `json:"-"`
	Modified     string `json:"-"`
	Rank         int    `json:"rank"`
	AboveConsent int    `json:"above_consent"`
	Total        int    `json:"total"`
}

func (e *Engine) Paused() bool {
	var value string
	_ = e.db.QueryRow("SELECT value FROM settings WHERE key='paused'").Scan(&value)
	return value == "1"
}
func (e *Engine) SetPaused(paused bool) error {
	value := "0"
	if paused {
		value = "1"
	}
	_, err := e.db.Exec("INSERT INTO settings(key,value) VALUES('paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", value)
	e.Wake()
	return err
}

type Engine struct {
	db           *sql.DB
	archive      *sql.DB
	archivePath  string
	archiveError string
	mu           sync.RWMutex
	client       *http.Client
	wake         chan struct{}
}

func Open(dir, archive string) (*Engine, error) {
	p := filepath.Join(dir, "monitor.sqlite")
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(p)+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=foreign_keys(1)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY, applicant TEXT NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL, updated TEXT NOT NULL DEFAULT '', checked TEXT NOT NULL DEFAULT '', next TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', failures INTEGER NOT NULL DEFAULT 0, etag TEXT NOT NULL DEFAULT '', modified TEXT NOT NULL DEFAULT '', UNIQUE(applicant,url));
CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE, at TEXT NOT NULL, rank INTEGER NOT NULL, above_consent INTEGER NOT NULL, total INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS changes_watch ON changes(watch_id,id);`)
	if err != nil {
		db.Close()
		return nil, err
	}
	e := &Engine{db: db, wake: make(chan struct{}, 1), client: &http.Client{Timeout: 25 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}}
	explicitArchive := archive != ""
	if !explicitArchive {
		_ = db.QueryRow("SELECT value FROM settings WHERE key='archive'").Scan(&archive)
	}
	if archive != "" {
		if err := e.SetArchive(archive); err != nil {
			if explicitArchive {
				db.Close()
				return nil, fmt.Errorf("archive: %w", err)
			}
			e.archiveError = "Архив недоступен. Подключите файл заново в настройках: " + err.Error()
		}
	}
	return e, nil
}

func (e *Engine) Close() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.archive != nil {
		e.archive.Close()
	}
	e.db.Close()
}

func (e *Engine) SetArchive(path string) error {
	abs, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	info, err := os.Stat(abs)
	if err != nil || !info.Mode().IsRegular() {
		return errors.New("выберите существующий файл SQLite")
	}
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(abs)}
	if !strings.HasPrefix(u.Path, "/") {
		u.Path = "/" + u.Path
	}
	db, err := sql.Open("sqlite", u.String()+"?mode=ro&_pragma=query_only(1)&_pragma=busy_timeout(5000)")
	if err != nil {
		return err
	}
	db.SetMaxOpenConns(2)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	rows, err := db.QueryContext(ctx, "SELECT c.id,c.program,c.university,c.seats,e.applicant_id,e.ordinal,e.has_consent FROM competitions c JOIN entries e ON e.competition_id=c.id LIMIT 0")
	if err != nil {
		db.Close()
		return errors.New("неподдерживаемая схема: нужны таблицы или представления competitions и entries")
	}
	rows.Close()
	e.mu.Lock()
	defer e.mu.Unlock()
	if _, err = e.db.Exec("INSERT INTO settings(key,value) VALUES('archive',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", abs); err != nil {
		db.Close()
		return err
	}
	if e.archive != nil {
		e.archive.Close()
	}
	e.archive = db
	e.archivePath = abs
	e.archiveError = ""
	return nil
}

func validID(id string) bool {
	if len(id) < 1 || len(id) > 32 {
		return false
	}
	for _, c := range id {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

type Match struct {
	Year         int    `json:"year"`
	Competition  int64  `json:"competition_id"`
	Title        string `json:"title"`
	University   string `json:"university"`
	Quota        string `json:"quota"`
	Seats        int    `json:"seats"`
	Rank         int    `json:"rank"`
	Consent      bool   `json:"consent"`
	AboveConsent int    `json:"above_consent"`
	Updated      string `json:"updated_at"`
}

func (e *Engine) Lookup(ctx context.Context, id string) ([]Match, error) {
	if !validID(id) {
		return nil, errors.New("ID должен содержать от 1 до 32 цифр")
	}
	e.mu.RLock()
	defer e.mu.RUnlock()
	if e.archive == nil {
		return nil, errors.New("сначала подключите исторический архив в настройках")
	}
	rows, err := e.archive.QueryContext(ctx, `SELECT CASE WHEN a.applicant_id LIKE '2026:%' THEN 2026 ELSE 2025 END,c.id,COALESCE(c.program,''),COALESCE(c.university,''),COALESCE(c.quota_type,''),COALESCE(c.seats,0),COALESCE(a.ordinal,0),a.has_consent,
(SELECT COUNT(*) FROM entries b WHERE b.competition_id=a.competition_id AND b.ordinal<a.ordinal AND b.has_consent=1),COALESCE(c.imported_at,'')
FROM entries a JOIN competitions c ON c.id=a.competition_id WHERE a.applicant_id IN (?,?) ORDER BY c.university,c.program LIMIT 600`, id, "2026:"+id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]Match, 0)
	for rows.Next() {
		var m Match
		if err := rows.Scan(&m.Year, &m.Competition, &m.Title, &m.University, &m.Quota, &m.Seats, &m.Rank, &m.Consent, &m.AboveConsent, &m.Updated); err != nil {
			return nil, err
		}
		result = append(result, m)
	}
	return result, rows.Err()
}

func (e *Engine) Watches(ctx context.Context) ([]Watch, error) {
	rows, err := e.db.QueryContext(ctx, "SELECT id,applicant,url,payload,updated,checked,next,error,failures,etag,modified FROM watches ORDER BY id DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]Watch, 0)
	for rows.Next() {
		var w Watch
		var payload string
		if err := rows.Scan(&w.ID, &w.Applicant, &w.URL, &payload, &w.Updated, &w.Checked, &w.Next, &w.Error, &w.Failures, &w.ETag, &w.Modified); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(payload), &w.List); err != nil {
			return nil, err
		}
		result = append(result, w)
	}
	return result, rows.Err()
}

func (e *Engine) AddWatch(ctx context.Context, id, source, title string, seats int) error {
	if !validID(id) {
		return errors.New("некорректный ID")
	}
	u, err := sourceURL(source)
	if err != nil {
		return err
	}
	if len(title) > 200 || seats < 0 || seats > 100000 {
		return errors.New("проверьте название и количество мест")
	}
	var count int
	if err = e.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM watches").Scan(&count); err != nil {
		return err
	}
	if count >= 50 {
		return errors.New("можно наблюдать не более 50 списков")
	}
	if title == "" {
		title = "Конкурсный список"
	}
	payload, _ := json.Marshal(List{Title: title, Seats: seats, Entries: []Entry{}})
	_, err = e.db.ExecContext(ctx, "INSERT INTO watches(applicant,url,payload) VALUES(?,?,?)", id, u, string(payload))
	if err != nil {
		return errors.New("этот ID и список уже добавлены или база недоступна")
	}
	e.Wake()
	return nil
}

func (e *Engine) Wake() {
	select {
	case e.wake <- struct{}{}:
	default:
	}
}

func Rank(l List, id string) (rank, above int) {
	for _, r := range l.Entries {
		if r.ID == id {
			rank = r.Rank
			break
		}
	}
	if rank > 0 {
		for _, r := range l.Entries {
			if r.Rank > 0 && r.Rank < rank && r.Consent {
				above++
			}
		}
	}
	return
}
