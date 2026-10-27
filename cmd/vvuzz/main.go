package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/LeoTechPro/Vvuzz/internal/monitor"
)

const version = "0.0.1"

func main() {
	root, err := os.UserConfigDir()
	if err != nil {
		log.Fatal(err)
	}
	data := flag.String("data", filepath.Join(root, "Vvuzz"), "local state directory")
	archive := flag.String("archive", "", "optional normalized historical SQLite (read-only)")
	port := flag.Int("port", 9177, "loopback port")
	noTray := flag.Bool("no-tray", false, "run without a desktop tray")
	showVersion := flag.Bool("version", false, "print version")
	removeData := flag.Bool("remove-local-data", false, "remove only Vvuzz state after the application has stopped")
	checkUnlocked := flag.Bool("check-data-unlocked", false, "check that Vvuzz is stopped before uninstalling")
	flag.Parse()
	if *checkUnlocked {
		if _, err := os.Stat(*data); os.IsNotExist(err) {
			return
		}
		unlock, err := lockData(*data)
		if err != nil {
			log.Fatal(err)
		}
		unlock()
		return
	}
	if *removeData {
		if err := removeLocalData(*data); err != nil {
			log.Fatal(err)
		}
		return
	}
	if *showVersion {
		fmt.Println(version)
		return
	}
	if *port < 1024 || *port > 65535 {
		log.Fatal("port must be between 1024 and 65535")
	}
	if err := os.MkdirAll(*data, 0700); err != nil {
		log.Fatal(err)
	}
	unlock, err := lockData(*data)
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if unlock != nil {
			unlock()
		}
	}()
	engine, err := monitor.Open(*data, *archive)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	listener, err := net.Listen("tcp4", fmt.Sprintf("127.0.0.1:%d", *port))
	if err != nil {
		log.Fatalf("Vvuzz cannot open port (another instance may be running): %v", err)
	}
	address := "http://" + listener.Addr().String()
	uninstall := &uninstaller{data: *data, stop: stop}
	server := &http.Server{Handler: engine.Handler(listener.Addr().String(), uninstall), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 20 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
	workerDone := make(chan struct{})
	go func() { defer close(workerDone); engine.Run(ctx) }()
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			log.Print(err)
			stop()
		}
	}()
	log.Printf("Vvuzz %s: %s (local data: %s)", version, address, *data)
	if *noTray {
		<-ctx.Done()
	} else {
		runTray(ctx, stop, address, engine)
	}
	stop()
	shutdown, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdown)
	<-workerDone
	engine.Close()
	unlock()
	unlock = nil
	if uninstall.removeData.Load() {
		if err := removeLocalData(*data); err != nil {
			log.Print("cannot remove local data: ", err)
		}
	}
}
