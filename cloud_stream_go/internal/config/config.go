package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
)

type Config struct {
	Listen     string `json:"listen"`
	PublicHost string `json:"public_host"`
	APIToken   string `json:"api_token"`

	RTMPPort int `json:"rtmp_port"`
	HLSPort  int `json:"hls_port"`

	FFmpegPath string `json:"ffmpeg_path"`
	LogDir     string `json:"log_dir"`
}

func Default() Config {
	return Config{
		Listen:     ":8088",
		PublicHost: "<YOUR_SERVER_IP>",
		APIToken:   "change-this-token",
		RTMPPort:   1935,
		HLSPort:    8888,
		FFmpegPath: `C:\nsy-cloud-stream\bin\ffmpeg.exe`,
		LogDir:     `C:\nsy-cloud-stream\logs`,
	}
}

func Load(path string) (Config, error) {
	cfg := Default()
	if path == "" {
		path = os.Getenv("NSY_CLOUD_STREAM_CONFIG")
	}
	if path == "" {
		path = "config.json"
	}
	if data, err := os.ReadFile(path); err == nil {
		if err := json.Unmarshal(data, &cfg); err != nil {
			return cfg, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return cfg, err
	}

	applyEnv(&cfg)
	if cfg.PublicHost == "" {
		return cfg, errors.New("public_host is required")
	}
	if cfg.RTMPPort <= 0 {
		cfg.RTMPPort = 1935
	}
	if cfg.HLSPort <= 0 {
		cfg.HLSPort = 8888
	}
	if cfg.Listen == "" {
		cfg.Listen = ":8088"
	}
	if cfg.LogDir == "" {
		cfg.LogDir = filepath.Join(".", "logs")
	}
	return cfg, nil
}

func applyEnv(cfg *Config) {
	if v := os.Getenv("NSY_CLOUD_PUBLIC_HOST"); v != "" {
		cfg.PublicHost = v
	}
	if v := os.Getenv("NSY_CLOUD_API_TOKEN"); v != "" {
		cfg.APIToken = v
	}
	if v := os.Getenv("NSY_CLOUD_LISTEN"); v != "" {
		cfg.Listen = v
	}
	if v := os.Getenv("NSY_CLOUD_FFMPEG"); v != "" {
		cfg.FFmpegPath = v
	}
	if v := os.Getenv("NSY_CLOUD_LOG_DIR"); v != "" {
		cfg.LogDir = v
	}
	if v := os.Getenv("NSY_CLOUD_RTMP_PORT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			cfg.RTMPPort = n
		}
	}
	if v := os.Getenv("NSY_CLOUD_HLS_PORT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			cfg.HLSPort = n
		}
	}
}

