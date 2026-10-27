package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

func uninstallCommand(data, mode string) (*exec.Cmd, bool, error) {
	exe, err := os.Executable()
	if err != nil {
		return nil, false, err
	}
	uninstall := filepath.Join(filepath.Dir(exe), "unins000.exe")
	if _, err = os.Stat(uninstall); err != nil {
		return nil, false, fmt.Errorf("Не найден установщик удаления. Установите обновлённую версию Vvuzz поверх текущей: %w", err)
	}
	data, err = filepath.Abs(data)
	if err != nil {
		return nil, false, err
	}
	return exec.Command(uninstall, "/VVUZZMODE="+mode, "/VVUZZPID="+strconv.Itoa(os.Getpid()), "/VVUZZDATA="+data), false, nil
}
