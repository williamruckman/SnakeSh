"""Expose bounded CI failure details through public GitHub check annotations."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def report(path: Path) -> None:
    if not path.is_file():
        print("::error::Windows terminal test report was not produced; inspect the failed setup step.")
        return
    for case in ET.parse(path).iter("testcase"):
        for kind in ("failure", "error"):
            problem = case.find(kind)
            if problem is None:
                continue
            message = f"{case.get('name')}: {problem.text or problem.get('message', '')}"[-6500:]
            message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Windows terminal integration::{message}")


if __name__ == "__main__":
    report(Path(sys.argv[1]))
