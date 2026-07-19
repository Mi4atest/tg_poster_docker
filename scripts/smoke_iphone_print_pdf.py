"""Смоук генерации PDF из живой БД."""
from pathlib import Path

from app.utils.iphone_print_pdf import build_iphone_price_pdf_bytes, pdf_page_count
from app.utils.iphone_print_price import build_print_catalog, fetch_new_iphones, fetch_tradein_iphones


def main() -> None:
    news = fetch_new_iphones()
    trades = fetch_tradein_iphones()
    print(f"new={len(news)} tradein_raw={len(trades)}")
    left, right, tradein = build_print_catalog(news, trades)
    print(
        f"left={sum(1 for L in left if not L.is_blank)} "
        f"right={sum(1 for L in right if not L.is_blank)} "
        f"tradein={len(tradein)}"
    )
    print("--- LEFT ---")
    for L in left:
        print(L.text if L.text else "<blank>")
    print("--- RIGHT ---")
    for L in right:
        print(L.text if L.text else "<blank>")
    print("--- TRADEIN ---")
    for L in tradein:
        print(L.text)
    pdf = build_iphone_price_pdf_bytes()
    out = Path("/tmp/iphone_prices_test.pdf")
    out.write_bytes(pdf)
    print(f"pdf_bytes={len(pdf)} pages~={pdf_page_count(pdf)} path={out}")


if __name__ == "__main__":
    main()
