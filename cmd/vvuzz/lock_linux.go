package main

import (
	"fmt"
	"os"
	"path/filepath"

	"golang.org/x/sys/unix"
)

func lockData(dir string) (func(), error) {
	f, err := os.OpenFile(filepath.Join(dir, ".vvuzz.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err = unix.Flock(int(f.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		f.Close()
		return nil, fmt.Errorf("каталог данных уже используется Vvuzz: %w", err)
	}
	return func() { f.Close() }, nil
}
