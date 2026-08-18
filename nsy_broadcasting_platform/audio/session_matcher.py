from __future__ import annotations

import threading

from nsy_broadcasting_platform.compat import PSUTIL, PYCAW

psutil = PSUTIL.module
pycaw_mod = PYCAW.module


class SessionMatcher:
    """使用 pycaw 识别并临时隔离目标窗口音频会话。"""

    def __init__(self) -> None:
        self.target_pid: int | None = None
        self.target_process: str | None = None
        self.strict_isolation = False
        self._lock = threading.RLock()
        self._saved_mutes: dict[tuple[int, str], bool] = {}
        self._isolation_note = ""

    def set_target(self, pid: int | None, process_name: str | None, strict_isolation: bool = False) -> None:
        with self._lock:
            self.target_pid = pid
            self.target_process = process_name.lower() if process_name else None
            self.strict_isolation = bool(strict_isolation and (pid is not None or process_name))
            if not self.strict_isolation:
                self.restore_isolation()
                self._isolation_note = ""

    @staticmethod
    def _process_name(proc, pid: int | None) -> str | None:
        try:
            return proc.name().lower()
        except Exception:
            if pid is not None and psutil is not None:
                try:
                    return psutil.Process(pid).name().lower()
                except Exception:
                    return None
        return None

    def _session_key(self, sess) -> tuple[int, str] | None:
        proc = getattr(sess, "Process", None)
        if proc is None:
            return None
        try:
            pid = int(proc.pid)
        except Exception:
            return None
        name = self._process_name(proc, pid) or ""
        return pid, name

    def _session_matches_target(self, key: tuple[int, str] | None) -> bool:
        if key is None:
            return False
        pid, name = key
        if self.target_pid is not None and pid == self.target_pid:
            return True
        return bool(self.target_process and self.target_process in name)

    @staticmethod
    def _session_volume(sess):
        return getattr(sess, "SimpleAudioVolume", None)

    def check_hit(self) -> bool:
        with self._lock:
            if pycaw_mod is None or (self.target_pid is None and not self.target_process):
                return False
            try:
                sessions = pycaw_mod.AudioUtilities.GetAllSessions()
            except Exception:
                return False
            return any(self._session_matches_target(self._session_key(sess)) for sess in sessions)

    def isolation_note(self) -> str:
        with self._lock:
            return self._isolation_note

    def restore_isolation(self) -> None:
        """恢复被本程序临时静音的非目标会话。"""
        with self._lock:
            if pycaw_mod is None or not self._saved_mutes:
                self._saved_mutes.clear()
                return
            try:
                sessions = pycaw_mod.AudioUtilities.GetAllSessions()
            except Exception:
                self._saved_mutes.clear()
                return

            for sess in sessions:
                key = self._session_key(sess)
                if key not in self._saved_mutes:
                    continue
                volume = self._session_volume(sess)
                if volume is None:
                    continue
                try:
                    volume.SetMute(int(self._saved_mutes[key]), None)
                except Exception:
                    pass
            self._saved_mutes.clear()

    def apply_isolation(self) -> bool:
        """隔离非目标会话，返回目标会话是否命中。"""
        with self._lock:
            if not self.strict_isolation:
                self._isolation_note = ""
                return self.check_hit()
            if pycaw_mod is None:
                self._isolation_note = "pycaw 不可用，严格窗口音轨已改为静音保底"
                return False

            try:
                sessions = pycaw_mod.AudioUtilities.GetAllSessions()
            except Exception:
                self._isolation_note = "读取音频会话失败，严格窗口音轨已改为静音保底"
                return False

            session_items = [(sess, self._session_key(sess)) for sess in sessions]
            target_keys = {key for _sess, key in session_items if self._session_matches_target(key)}
            if not target_keys:
                self.restore_isolation()
                self._isolation_note = "未命中目标窗口音轨，严格模式下静音保底"
                return False

            muted_count = 0
            restored_count = 0
            for sess, key in session_items:
                if key is None:
                    continue
                volume = self._session_volume(sess)
                if volume is None:
                    continue
                try:
                    if key in target_keys:
                        if key in self._saved_mutes:
                            volume.SetMute(int(self._saved_mutes.pop(key)), None)
                            restored_count += 1
                        continue
                    if key not in self._saved_mutes:
                        self._saved_mutes[key] = bool(volume.GetMute())
                    volume.SetMute(1, None)
                    muted_count += 1
                except Exception:
                    continue

            self._isolation_note = f"严格窗口音轨: 命中 {len(target_keys)} 个会话，已屏蔽 {muted_count} 个其他会话"
            if restored_count:
                self._isolation_note += f"，恢复 {restored_count} 个目标会话"
            return True
