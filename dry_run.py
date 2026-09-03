import asyncio
import sys
from datetime import timedelta

import service
from week import today


async def run(offset: int) -> None:
    target = today() + timedelta(days=offset)
    label = "СЕГОДНЯ" if offset == 0 else "ЗАВТРА"
    print(f"===== Расписание на {label} ({target}) =====")
    try:
        print(await service.get_day_view(target))
    except Exception:
        import traceback
        traceback.print_exc()
    print()


async def main() -> None:
    offsets = [int(a) for a in sys.argv[1:]] or [0, 1]
    for offset in offsets:
        await run(offset)


if __name__ == "__main__":
    asyncio.run(main())
