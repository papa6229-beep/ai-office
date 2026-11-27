"""Local Windows metrics for the single selected Times Gate PC display.

Uses the documented Divoom PC packet and existing serialized transport.
No model, cloud metrics, sensor driver, or user input is involved.
"""
import ctypes
import shutil
import subprocess
import threading
import time


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong) for name in
        ('total_physical', 'available_physical', 'total_page', 'available_page',
         'total_virtual', 'available_virtual', 'available_extended')]


class PCMonitor:
    def __init__(self, display, picker):
        self.display = display
        self.picker = picker
        self.lock = threading.Lock()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.previous_cpu = None
        self.nvidia = shutil.which('nvidia-smi')
        self.selected = None
        self.status = {'enabled': True, 'screen': 5, 'interval_seconds': 5,
                       'updates': 0, 'error': '', 'model_calls': 0}

    def sample(self):
        cpu = '--'
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if self.kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            current = (idle.value, kernel.value + user.value)
            if self.previous_cpu and current[1] > self.previous_cpu[1]:
                elapsed = current[1] - self.previous_cpu[1]
                busy = elapsed - (current[0] - self.previous_cpu[0])
                cpu = f'{max(0, min(100, round(100 * busy / elapsed)))}%'
            self.previous_cpu = current
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        ram = f'{memory.load}%' if self.kernel.GlobalMemoryStatusEx(ctypes.byref(memory)) else '--'
        gpu, temperature = '--', '--'
        if self.nvidia:
            try:
                result = subprocess.run([self.nvidia, '--id=0',
                    '--query-gpu=utilization.gpu,temperature.gpu', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=3,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode == 0:
                    values = result.stdout.strip().splitlines()[0].split(',')
                    load, degrees = (float(value.strip()) for value in values)
                    if 0 <= load <= 100:
                        gpu = f'{round(load)}%'
                    if -20 <= degrees <= 150:
                        temperature = f'{round(degrees)}C'
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                pass
        # Protocol slots: CPU load, GPU load, CPU temp, GPU temp, memory, disk temp.
        return [cpu, gpu, '--', temperature, ram, '--']

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def run(self):
        self.sample()  # Baseline for a real interval CPU reading, not a fake zero.
        while not self.display.stop.wait(5):
            try:
                with self.picker.lock:
                    choice = self.picker.current(5)
                    previewing = self.picker.pending is not None
                    connection = dict(self.picker.connection_recovery)
                if previewing or choice.get('clock_id') != 625 or not connection.get('connected'):
                    self.selected = None
                    continue
                if connection.get('light_switch') == 0:
                    continue
                selection = (self.display.ip, connection.get('last_restored'))
                if selection != self.selected:
                    self.picker.render(choice)
                    self.selected = selection
                values = self.sample()
                self.display.command({'Command': 'Device/UpdatePCParaInfo',
                    'ScreenList': [{'LcdId': 4, 'DispData': values}]})
                with self.lock:
                    self.status.update(updates=self.status['updates'] + 1,
                        values=values, last_updated=time.time(), error='')
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                with self.lock:
                    self.status['error'] = type(error).__name__ + ': ' + str(error)[:150]
                self.selected = None
