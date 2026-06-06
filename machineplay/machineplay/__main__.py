import asyncio

from machineplay.client import run_forever


def main():
    print("Welcome")
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("shutting down")


if __name__ == "__main__":
    main()
