"""Interactive chat loop for manual testing."""

from agent import Agent


def main():
    try:
        agent = Agent()
    except ValueError as exc:  # missing config gets a hint, not a traceback
        raise SystemExit(f"config error: {exc}") from None
    print("Payment collection agent. Type your message (Ctrl-C to exit).")
    while True:
        try:
            line = input("you:   ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        print("agent:", agent.next(line)["message"])
        if agent.session.state.name in ("CLOSED", "LOCKED"):
            break  # conversation is over


if __name__ == "__main__":
    main()
