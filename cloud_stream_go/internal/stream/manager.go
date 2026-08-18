package stream

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"nsy-cloud-stream/internal/config"
)

type URLInfo struct {
	App       string `json:"app"`
	Stream    string `json:"stream"`
	PushURL   string `json:"push_url"`
	PlayRTMP  string `json:"play_rtmp"`
	PlayHLS   string `json:"play_hls"`
	LocalInput string `json:"local_input"`
}

type RelayStartRequest struct {
	ID        string `json:"id"`
	App       string `json:"app"`
	Stream    string `json:"stream"`
	InputURL  string `json:"input_url"`
	TargetURL string `json:"target_url"`
}

type RelayStatus struct {
	ID        string `json:"id"`
	InputURL  string `json:"input_url"`
	TargetURL string `json:"target_url"`
	Running   bool   `json:"running"`
	StartedAt string `json:"started_at"`
	StoppedAt string `json:"stopped_at"`
	LastError string `json:"last_error"`
	LogFile   string `json:"log_file"`
}

type relayProcess struct {
	status RelayStatus
	cancel context.CancelFunc
	cmd    *exec.Cmd
}

type Manager struct {
	cfg    config.Config
	mu     sync.Mutex
	relays map[string]*relayProcess
}

func NewManager(cfg config.Config) *Manager {
	return &Manager{cfg: cfg, relays: make(map[string]*relayProcess)}
}

func (m *Manager) BuildURL(app, stream string) URLInfo {
	app = cleanPathPart(app, "live")
	stream = cleanPathPart(stream, "main")
	path := app + "/" + stream
	return URLInfo{
		App:        app,
		Stream:     stream,
		PushURL:    fmt.Sprintf("rtmp://%s:%d/%s", m.cfg.PublicHost, m.cfg.RTMPPort, path),
		PlayRTMP:   fmt.Sprintf("rtmp://%s:%d/%s", m.cfg.PublicHost, m.cfg.RTMPPort, path),
		PlayHLS:    fmt.Sprintf("http://%s:%d/%s/index.m3u8", m.cfg.PublicHost, m.cfg.HLSPort, path),
		LocalInput: fmt.Sprintf("rtmp://127.0.0.1:%d/%s", m.cfg.RTMPPort, path),
	}
}

func cleanPathPart(value, fallback string) string {
	value = strings.TrimSpace(value)
	value = strings.Trim(value, "/\\")
	if value == "" {
		return fallback
	}
	value = strings.Map(func(r rune) rune {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '-' {
			return r
		}
		return '-'
	}, value)
	if value == "" {
		return fallback
	}
	return value
}

func (m *Manager) StartRelay(req RelayStartRequest) (RelayStatus, error) {
	target := strings.TrimSpace(req.TargetURL)
	if target == "" {
		return RelayStatus{}, errors.New("target_url is required")
	}
	if _, err := url.ParseRequestURI(target); err != nil {
		return RelayStatus{}, fmt.Errorf("invalid target_url: %w", err)
	}
	id := cleanPathPart(req.ID, "main")
	urls := m.BuildURL(req.App, req.Stream)
	input := strings.TrimSpace(req.InputURL)
	if input == "" {
		input = urls.LocalInput
	}

	m.mu.Lock()
	if old := m.relays[id]; old != nil {
		stopRelayLocked(old, "replaced")
		delete(m.relays, id)
	}
	m.mu.Unlock()

	if err := os.MkdirAll(m.cfg.LogDir, 0755); err != nil {
		return RelayStatus{}, err
	}
	logFile := filepath.Join(m.cfg.LogDir, "relay-"+id+".log")
	log, err := os.OpenFile(logFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return RelayStatus{}, err
	}

	ctx, cancel := context.WithCancel(context.Background())
	args := []string{
		"-hide_banner",
		"-loglevel", "warning",
		"-fflags", "nobuffer",
		"-i", input,
		"-map", "0:v:0",
		"-map", "0:a?",
		"-c:v", "copy",
		"-c:a", "aac",
		"-ar", "48000",
		"-b:a", "128k",
		"-f", "flv",
		target,
	}
	cmd := exec.CommandContext(ctx, m.cfg.FFmpegPath, args...)
	cmd.Stdout = log
	cmd.Stderr = log

	status := RelayStatus{
		ID:        id,
		InputURL:  input,
		TargetURL: target,
		Running:   true,
		StartedAt: time.Now().Format(time.RFC3339),
		LogFile:   logFile,
	}
	proc := &relayProcess{status: status, cancel: cancel, cmd: cmd}

	if err := cmd.Start(); err != nil {
		cancel()
		_ = log.Close()
		status.Running = false
		status.LastError = err.Error()
		return status, err
	}

	m.mu.Lock()
	m.relays[id] = proc
	m.mu.Unlock()

	go func() {
		err := cmd.Wait()
		_ = log.Close()
		m.mu.Lock()
		defer m.mu.Unlock()
		if current := m.relays[id]; current == proc {
			proc.status.Running = false
			proc.status.StoppedAt = time.Now().Format(time.RFC3339)
			if err != nil && !errors.Is(ctx.Err(), context.Canceled) {
				proc.status.LastError = err.Error()
			}
		}
	}()

	return status, nil
}

func (m *Manager) StopRelay(id string) (RelayStatus, bool) {
	id = cleanPathPart(id, "main")
	m.mu.Lock()
	defer m.mu.Unlock()
	proc := m.relays[id]
	if proc == nil {
		return RelayStatus{ID: id, Running: false, LastError: "relay not found"}, false
	}
	stopRelayLocked(proc, "stopped")
	delete(m.relays, id)
	return proc.status, true
}

func (m *Manager) ListRelays() []RelayStatus {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]RelayStatus, 0, len(m.relays))
	for _, proc := range m.relays {
		out = append(out, proc.status)
	}
	return out
}

func (m *Manager) StopAll() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for id, proc := range m.relays {
		stopRelayLocked(proc, "shutdown")
		delete(m.relays, id)
	}
}

func stopRelayLocked(proc *relayProcess, reason string) {
	if proc.cancel != nil {
		proc.cancel()
	}
	if runtime.GOOS == "windows" && proc.cmd != nil && proc.cmd.Process != nil {
		_ = exec.Command("taskkill", "/PID", fmt.Sprint(proc.cmd.Process.Pid), "/T", "/F").Run()
	}
	proc.status.Running = false
	proc.status.StoppedAt = time.Now().Format(time.RFC3339)
	if reason != "" {
		proc.status.LastError = reason
	}
}
