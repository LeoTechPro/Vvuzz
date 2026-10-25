package monitor

import (
	"context"
	"embed"
	"encoding/json"
	"io/fs"
	"net/http"
	"strconv"
	"time"
)

//go:embed ui/*
var assets embed.FS

func (e *Engine) Handler(host string) http.Handler {
	mux := http.NewServeMux()
	jsonOut := func(w http.ResponseWriter, value any) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(value)
	}
	fail := func(w http.ResponseWriter, err error) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusBadRequest)
		jsonOut(w, map[string]string{"error": err.Error()})
	}
	mux.HandleFunc("GET /api/state", func(w http.ResponseWriter, r *http.Request) {
		watches, err := e.Watches(r.Context())
		if err != nil {
			fail(w, err)
			return
		}
		for i := range watches {
			w := &watches[i]
			w.Rank, w.AboveConsent = Rank(w.List, w.Applicant)
			w.Total = len(w.List.Entries)
			w.List.Entries = nil
		}
		e.mu.RLock()
		archive := e.archivePath
		archiveError := e.archiveError
		e.mu.RUnlock()
		cooldown := ""
		if at := e.SourceCooldown(); time.Now().Before(at) {
			cooldown = at.Format(time.RFC3339)
		}
		jsonOut(w, map[string]any{"version": "0.0.1", "archive": archive, "archive_error": archiveError, "source_cooldown": cooldown, "watches": watches, "paused": e.Paused(), "interval_minutes": 30, "history_limit": 2000})
	})
	mux.HandleFunc("POST /api/pause", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Paused bool `json:"paused"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			fail(w, err)
			return
		}
		if err := e.SetPaused(body.Paused); err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /api/lookup", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
		defer cancel()
		matches, err := e.Lookup(ctx, r.URL.Query().Get("id"))
		if err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, matches)
	})
	mux.HandleFunc("POST /api/archive", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Path string `json:"path"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			fail(w, err)
			return
		}
		if err := e.SetArchive(body.Path); err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("POST /api/watches", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			ID    string `json:"applicant_id"`
			URL   string `json:"url"`
			Title string `json:"title"`
			Seats int    `json:"seats"`
			Rules bool   `json:"rules_reviewed"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			fail(w, err)
			return
		}
		if !body.Rules {
			http.Error(w, "подтвердите правила источника", 400)
			return
		}
		if err := e.AddWatch(r.Context(), body.ID, body.URL, body.Title, body.Seats); err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("DELETE /api/watches/{id}", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			fail(w, err)
			return
		}
		if _, err = e.db.ExecContext(r.Context(), "DELETE FROM watches WHERE id=?", id); err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /api/history/{id}", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			fail(w, err)
			return
		}
		rows, err := e.db.QueryContext(r.Context(), "SELECT at,rank,above_consent,total FROM changes WHERE watch_id=? ORDER BY id DESC LIMIT 100", id)
		if err != nil {
			fail(w, err)
			return
		}
		defer rows.Close()
		result := make([]map[string]any, 0)
		for rows.Next() {
			var at string
			var rank, above, total int
			if err = rows.Scan(&at, &rank, &above, &total); err != nil {
				fail(w, err)
				return
			}
			result = append(result, map[string]any{"at": at, "rank": rank, "above_consent": above, "total": total})
		}
		if err = rows.Err(); err != nil {
			fail(w, err)
			return
		}
		jsonOut(w, result)
	})
	static, _ := fs.Sub(assets, "ui")
	mux.Handle("GET /", http.FileServer(http.FS(static)))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		if r.Host != host {
			http.Error(w, "invalid host", 403)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+host {
			http.Error(w, "cross-origin denied", 403)
			return
		}
		if r.Method != "GET" && r.Method != "HEAD" {
			if r.Header.Get("X-Vvuzz-Local") != "1" {
				http.Error(w, "local request required", 403)
				return
			}
			r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
		}
		mux.ServeHTTP(w, r)
	})
}
