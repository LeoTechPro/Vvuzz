package main

import (
	"fmt"
	"os"
	"path/filepath"

	"golang.org/x/sys/windows"
)

func lockData(dir string) (func(), error) {
	f, err := os.OpenFile(filepath.Join(dir, ".vvuzz.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	err = windows.LockFileEx(windows.Handle(f.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &windows.Overlapped{})
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("каталог данных уже используется Vvuzz: %w", err)
	}
	return func() { f.Close() }, nil
}
