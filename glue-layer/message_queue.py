# glue-layer/message_queue.py
import queue
import threading
import time
from typing import Callable, Optional, Tuple

class MessageQueue:
    """消息队列 + 排他锁，确保串行化发送"""

    def __init__(self, send_callback: Callable[[str, str], bool]):
        """
        Args:
            send_callback: 发送回调函数 (contact, message) -> success
        """
        self.queue: queue.Queue[Tuple[str, str, str]] = queue.Queue()
        self.lock = threading.Lock()
        self.send_callback = send_callback
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False

    def start(self):
        """启动工作线程"""
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        print("[MessageQueue] 工作线程已启动")

    def stop(self):
        """停止工作线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        print("[MessageQueue] 工作线程已停止")

    def enqueue(self, target: str, message: str, context: str):
        """将消息加入队列"""
        self.queue.put((target, message, context))
        print(f"[MessageQueue] 已入队: target={target}, message={message[:20]}...")

    def _worker(self):
        """工作线程：从队列取出消息，串行化发送"""
        while self.running:
            try:
                # 从队列取出（最多等待 1 秒）
                target, message, context = self.queue.get(timeout=1)

                print(f"[MessageQueue] 处理消息: {target}")

                # 获取锁，执行发送
                with self.lock:
                    success = self.send_callback(target, message)

                    if success:
                        print(f"[MessageQueue] 发送成功: {target}")
                    else:
                        print(f"[MessageQueue] 发送失败: {target}")

                self.queue.task_done()

            except queue.Empty:
                # 队列空，继续等待
                continue
            except Exception as e:
                print(f"[MessageQueue] 处理异常: {e}")
                time.sleep(1)

    def wait_empty(self, timeout: float = 30) -> bool:
        """等待队列清空"""
        try:
            self.queue.join()
            return True
        except Exception:
            return False