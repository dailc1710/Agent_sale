"""Seed or update the synthetic clinic profile used by local development only."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from hermes_secure_sales.config import get_settings
from hermes_secure_sales.db import SessionLocal
from hermes_secure_sales.models import Product, Shop, Tenant


DEMO_SLUG = "demo-shop"
DEMO_NAME = "Bệnh viện Tâm Trí Sài Gòn · Bản thử nghiệm"
WELCOME_MESSAGE = (
    "Xin chào! Đây là trợ lý thử nghiệm tham khảo thông tin của Khoa Tiêu hóa & Nội soi Tâm Trí Sài Gòn. "
    "Tôi có thể chia sẻ thông tin gói khám; giá cần nhân viên xác nhận. Nếu cần đặt lịch, vui lòng "
    "liên hệ trực tiếp với cơ sở qua số 0974 508 479. Tôi không chẩn đoán "
    "hay tư vấn điều trị qua chat."
)
SERVICES = {
    "DEMO-01": {
        "name": "Tầm soát ung thư dạ dày",
        "description": (
            "Theo website tham khảo: khám tiêu hóa, siêu âm bụng, X-quang ngực, điện tim, "
            "nội soi thực quản-dạ dày-tá tràng an thần và một số xét nghiệm. Giá tham khảo "
            "3.044.800 VND trên website ngày 14/10/2026; cần nhân viên xác nhận trước khi đặt."
        ),
        "price": Decimal("3044800.00"),
    },
    "DEMO-02": {
        "name": "Tầm soát ung thư đại trực tràng",
        "description": (
            "Theo website tham khảo: khám tiêu hóa, siêu âm bụng, X-quang ngực, điện tim, "
            "nội soi đại tràng an thần và một số xét nghiệm. Giá tham khảo 3.444.800 VND "
            "trên website ngày 14/10/2026; cần nhân viên xác nhận trước khi đặt."
        ),
        "price": Decimal("3444800.00"),
    },
    "DEMO-03": {
        "name": "Tầm soát ung thư dạ dày & đại trực tràng",
        "description": (
            "Theo website tham khảo: gói kết hợp nội soi dạ dày và đại tràng an thần cùng "
            "khám và xét nghiệm. Giá tham khảo 4.804.800 VND trên website ngày 14/10/2026; "
            "cần nhân viên xác nhận trước khi đặt."
        ),
        "price": Decimal("4804800.00"),
    },
}


def main() -> None:
    settings = get_settings()
    if settings.app_env != "development" or not settings.database_url.startswith("sqlite"):
        raise SystemExit("This helper is limited to development SQLite databases.")

    with SessionLocal() as db:
        shop = db.scalar(select(Shop).where(Shop.slug == DEMO_SLUG))
        if shop is None:
            tenant_id = uuid4()
            shop = Shop(
                tenant_id=tenant_id,
                slug=DEMO_SLUG,
                display_name=DEMO_NAME,
                welcome_message=WELCOME_MESSAGE,
                theme_color="#159fd1",
                allowed_origins="[]",
                enabled=True,
            )
            db.add(Tenant(id=tenant_id, name="Cơ sở thử nghiệm Tâm Trí Sài Gòn"))
            db.add(shop)
            db.flush()
        else:
            products = list(db.scalars(select(Product).where(Product.tenant_id == shop.tenant_id)))
            known_demo_skus = set(SERVICES) | {"DEMO-01", "DEMO-02"}
            if shop.display_name not in {"Demo Shop", DEMO_NAME} or any(
                product.sku not in known_demo_skus for product in products
            ):
                raise SystemExit("The demo profile has custom data; it was left unchanged.")
            shop.display_name = DEMO_NAME
            shop.welcome_message = WELCOME_MESSAGE
            shop.theme_color = "#159fd1"
            # Remove the old placeholder origin. The clinic site is not implicitly authorized to embed.
            shop.allowed_origins = "[]"
            tenant = db.get(Tenant, shop.tenant_id)
            if tenant:
                tenant.name = "Cơ sở thử nghiệm Tâm Trí Sài Gòn"

        products_by_sku = {
            product.sku: product
            for product in db.scalars(select(Product).where(Product.tenant_id == shop.tenant_id))
        }
        for sku, service in SERVICES.items():
            product = products_by_sku.get(sku)
            if product is None:
                product = Product(tenant_id=shop.tenant_id, sku=sku, currency="VND")
                db.add(product)
            product.name = service["name"]
            product.description = service["description"]
            product.price = service["price"]
            product.currency = "VND"
            product.active = True

        db.commit()
    print("Synthetic clinic profile and reference service catalog are ready.")


if __name__ == "__main__":
    main()

