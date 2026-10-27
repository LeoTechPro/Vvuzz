package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRemovalKeepsArchivesAndRefusesActiveData(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"monitor.sqlite", "monitor.sqlite-wal", "archive.sqlite"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(name), 0600); err != nil {
			t.Fatal(err)
		}
	}
	unlock, err := lockData(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := removeLocalData(dir); err == nil {
		t.Fatal("removed data while another instance holds the lock")
	}
	if _, err := os.Stat(filepath.Join(dir, "monitor.sqlite")); err != nil {
		t.Fatal(err)
	}
	unlock()
	if err := removeLocalData(dir); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "monitor.sqlite")); !os.IsNotExist(err) {
		t.Fatalf("state remains: %v", err)
	}
	if b, err := os.ReadFile(filepath.Join(dir, "archive.sqlite")); err != nil || string(b) != "archive.sqlite" {
		t.Fatalf("archive changed: %v", err)
	}
}
