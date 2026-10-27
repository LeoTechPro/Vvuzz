//go:build !headless

package main

// AppIndicator delegates pointer gestures to the Linux desktop shell.
func installTrayDoubleClick(_ func()) {}
