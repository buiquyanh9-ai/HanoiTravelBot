"""Chat trên terminal: python -m hanoibot.cli"""
import sys

from .bot import handle


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("Trợ lý du lịch Hà Nội (MVP). Gõ 'thoat' để dừng.")
    session: dict = {}  # giữ ngữ cảnh giữa các lượt
    while True:
        try:
            text = input("\nBạn: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("thoat", "exit", "quit"):
            break
        if not text:
            continue
        out = handle(text, session=session)
        print(f"\nBot ({out['latency_s']}s, {len(out['llm_calls'])} lần gọi LLM):\n{out['answer']}")


if __name__ == "__main__":
    main()
