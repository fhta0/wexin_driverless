# glue-layer/message_queue.py
import queue
import threading
import time
from typing import Callable, Optional, Tuple

class MessageQueue:
    """消息队列 + 排他锁，确保串行化发送"""

    def __init__(self, send_callback: Callable[[str, str], bool], log_fn: Optional[Callable[[str, str, Optional[str]], None]] = None):
        """
        Args:
            send_callback: 发送回调函数 (contact, message) -> success
            log_fn: 可选日志回调 (log_type, content, contact) 用于把关键节点推送到 SightFlow UI
        """
        self.queue: queue.Queue[Tuple[str, str, str]] = queue.Queue()
        self.lock = threading.Lock()
        self.send_callback = send_callback
        self.log_fn = log_fn
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False

    def _emit(self, log_type: str, content: str, contact: Optional[str] = None):
        """双写：print 到 stdout（保留给进程 stdout / ProcessManager）+ log_fn 到 SightFlow UI"""
        print(f"[MessageQueue] {content}", flush=True)
        if self.log_fn:
            try:
                self.log_fn(log_type, content, contact)
            except Exception:
                pass

    def start(self):
        """启动工作线程"""
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        self._emit('info', '工作线程已启动')

    def stop(self):
        """停止工作线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        self._emit('info', '工作线程已停止')

    def enqueue(self, target: str, message: str, context: str):
        """将消息加入队列"""
        self.queue.put((target, message, context))
        self._emit('info', f'已入队: {target}', target)

    def _worker(self):
        """工作线程：从队列取出消息，串行化发送"""
        while self.running:
            try:
                target, message, context = self.queue.get(timeout=1)

                self._emit('info', f'处理消息: {target}', target)

                with self.lock:
                    success = self.send_callback(target, message)

                    if success:
                        self._emit('send', f'发送成功: {target}', target)
                    else:
                        self._emit('error', f'发送失败: {target}', target)

                self.queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                self._emit('error', f'处理异常: {e}')
                time.sleep(1)

    def wait_empty(self, timeout: float = 30) -> bool:
        """等待队列清空"""
        try:
            self.queue.join()
            return True
        except Exception:
            return False