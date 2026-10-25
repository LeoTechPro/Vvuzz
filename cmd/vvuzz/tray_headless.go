//go:build headless || (!windows && !linux)

package main

import (
	"context"
	"github.com/LeoTechPro/Vvuzz/internal/monitor"
)

func runTray(ctx context.Context, _ context.CancelFunc, _ string, _ *monitor.Engine) { <-ctx.Done() }
