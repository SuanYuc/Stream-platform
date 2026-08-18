package main

import (
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"nsy-cloud-stream/internal/config"
	"nsy-cloud-stream/internal/stream"
)

type server struct {
	cfg config.Config
	mgr *stream.Manager
}

func main() {
	configPath := flag.String("config", "", "config file path")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("load config failed: %v", err)
	}
	mgr := stream.NewManager(cfg)
	s := &server{cfg: cfg, mgr: mgr}

	mux := http.NewServeMux()
	mux.HandleFunc("/api/health", s.withAuth(s.health))
	mux.HandleFunc("/api/urls", s.withAuth(s.urls))
	mux.HandleFunc("/api/relay/start", s.withAuth(s.relayStart))
	mux.HandleFunc("/api/relay/stop", s.withAuth(s.relayStop))
	mux.HandleFunc("/api/relay/status", s.withAuth(s.relayStatus))

	httpServer := &http.Server{
		Addr:              cfg.Listen,
		Handler:           withCORS(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		log.Printf("nsy cloud stream api listening on %s", cfg.Listen)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server failed: %v", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	log.Println("shutting down...")
	mgr.StopAll()
}

func (s *server) withAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.cfg.APIToken != "" && s.cfg.APIToken != "change-this-token" {
			header := r.Header.Get("Authorization")
			token := strings.TrimPrefix(header, "Bearer ")
			if token != s.cfg.APIToken {
				writeJSON(w, http.StatusUnauthorized, map[string]any{"ok": false, "error": "unauthorized"})
				return
			}
		}
		next(w, r)
	}
}

func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":          true,
		"service":     "nsy-cloud-stream",
		"public_host": s.cfg.PublicHost,
		"time":        time.Now().Format(time.RFC3339),
	})
}

func (s *server) urls(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	info := s.mgr.BuildURL(q.Get("app"), q.Get("stream"))
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "data": info})
}

func (s *server) relayStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method not allowed"})
		return
	}
	var req stream.RelayStartRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	status, err := s.mgr.StartRelay(req)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error(), "data": status})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "data": status})
}

func (s *server) relayStop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method not allowed"})
		return
	}
	var req struct {
		ID string `json:"id"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	status, ok := s.mgr.StopRelay(req.ID)
	writeJSON(w, http.StatusOK, map[string]any{"ok": ok, "data": status})
}

func (s *server) relayStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "data": s.mgr.ListRelays()})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

