//go:build !headless && (windows || linux)

package main

import (
	"context"
	_ "embed"
	"log"
	"os/exec"
	"runtime"
	"time"

	"github.com/LeoTechPro/Vvuzz/internal/monitor"
	"github.com/getlantern/systray"
)

//go:embed icon.ico
var iconICO []byte

//go:embed icon.png
var iconPNG []byte

func runTray(ctx context.Context, stop context.CancelFunc, address string, e *monitor.Engine) {
	systray.Run(func() {
		if runtime.GOOS == "windows" {
			systray.SetIcon(iconICO)
		} else {
			systray.SetIcon(iconPNG)
		}
		systray.SetTitle("Vvuzz")
		systray.SetTooltip("Vvuzz: локальное наблюдение за поступлением")
		open := systray.AddMenuItem("Открыть Vvuzz", "Открыть локальный интерфейс")
		pause := systray.AddMenuItemCheckbox("Приостановить наблюдение", "Сохраняет списки и историю", e.Paused())
		systray.AddSeparator()
		quit := systray.AddMenuItem("Завершить Vvuzz", "Остановить процесс наблюдения")
		go func() {
			ticker := time.NewTicker(10 * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					systray.Quit()
					return
				case <-open.ClickedCh:
					var command *exec.Cmd
					if runtime.GOOS == "windows" {
						command = exec.Command("rundll32", "url.dll,FileProtocolHandler", address)
					} else {
						command = exec.Command("xdg-open", address)
					}
					if err := command.Start(); err != nil {
						log.Print("cannot open browser: ", err)
					} else {
						go command.Wait()
					}
				case <-pause.ClickedCh:
					if err := e.SetPaused(!e.Paused()); err != nil {
						log.Print(err)
					}
				case <-ticker.C:
					if e.Paused() {
						pause.Check()
						systray.SetTooltip("Vvuzz: наблюдение приостановлено")
					} else {
						pause.Uncheck()
						systray.SetTooltip("Vvuzz: наблюдение работает")
					}
				case <-quit.ClickedCh:
					stop()
					systray.Quit()
					return
				}
			}
		}()
	}, func() { stop() })
}
