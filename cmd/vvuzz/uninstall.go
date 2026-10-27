package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

type uninstaller struct {
	data       string
	stop       func()
	mu         sync.Mutex
	status     string
	errorText  string
	removeData atomic.Bool
}

func (u *uninstaller) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	u.mu.Lock()
	defer u.mu.Unlock()
	if r.Method == http.MethodGet {
		_ = json.NewEncoder(w).Encode(map[string]string{"status": u.status, "error": u.errorText})
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "GET, POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var request struct {
		Confirmed bool   `json:"confirmed"`
		Mode      string `json:"mode"`
	}
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil || !request.Confirmed || (request.Mode != "keep" && request.Mode != "delete") {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "Подтвердите удаление и выберите, сохранить ли данные."})
		return
	}
	if u.status == "running" {
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "Удаление уже запущено."})
		return
	}
	command, waitForSuccess, err := uninstallCommand(u.data, request.Mode)
	if err == nil {
		err = command.Start()
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	u.status, u.errorText = "running", ""
	go func() {
		if waitForSuccess {
			if err := command.Wait(); err != nil {
				u.mu.Lock()
				u.status, u.errorText = "failed", "Удаление отменено или завершилось ошибкой. Данные сохранены."
				u.mu.Unlock()
				return
			}
			u.removeData.Store(request.Mode == "delete")
		} else {
			go command.Wait()
		}
		// Let the confirmation response reach the browser before shutdown.
		time.Sleep(300 * time.Millisecond)
		u.stop()
	}()
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

func removeLocalData(dir string) error {
	info, err := os.Lstat(dir)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("отказ удаления: каталог данных является ссылкой")
	}
	unlock, err := lockData(dir)
	if err != nil {
		return err
	}
	defer unlock()
	// Never recurse: downloaded archives and unrelated files must survive.
	var paths []string
	for _, name := range []string{"monitor.sqlite-wal", "monitor.sqlite-shm", "monitor.sqlite"} {
		path := filepath.Join(dir, name)
		info, err := os.Lstat(path)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("отказ удаления нестандартного файла: %s", name)
		}
		paths = append(paths, path)
	}
	for _, path := range paths {
		if err := os.Remove(path); err != nil {
			return err
		}
	}
	return nil
}
