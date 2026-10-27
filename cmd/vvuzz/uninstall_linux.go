package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func uninstallCommand(_, _ string) (*exec.Cmd, bool, error) {
	exe, err := os.Executable()
	if err != nil {
		return nil, false, err
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		return nil, false, err
	}
	if exe != "/usr/bin/vvuzz" {
		return nil, false, errors.New("Автоматическое удаление доступно для пакета .deb. Для переносной версии завершите Vvuzz и удалите её исполняемый файл.")
	}
	if _, err := exec.LookPath("pkexec"); err != nil {
		return nil, false, errors.New("Для удаления .deb требуется polkit (pkexec). Либо завершите Vvuzz и выполните sudo apt remove vvuzz.")
	}
	status, err := os.ReadFile("/proc/self/status")
	if err != nil {
		return nil, false, err
	}
	if strings.Contains(string(status), "NoNewPrivs:\t1") {
		if _, err := exec.LookPath("systemd-run"); err != nil {
			return nil, false, errors.New("Для удаления службой необходим systemd-run. Либо остановите Vvuzz и выполните sudo apt remove vvuzz.")
		}
		return exec.Command("systemd-run", "--user", "--wait", "--collect", "--pipe", "--quiet", "--", "pkexec", "/usr/bin/dpkg", "--remove", "vvuzz"), true, nil
	}
	// No privileged shell or user-controlled arguments are passed to polkit.
	return exec.Command("pkexec", "/usr/bin/dpkg", "--remove", "vvuzz"), true, nil
}
