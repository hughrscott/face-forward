from pathlib import Path


PROJECT_ROOT = Path(__file__).parent
STORE_PAGE = PROJECT_ROOT / "site" / "src" / "pages" / "store" / "index.astro"
HOME_PAGE = PROJECT_ROOT / "site" / "src" / "pages" / "index.astro"
SIGN_ASSET = (
    PROJECT_ROOT
    / "site"
    / "public"
    / "assets"
    / "merchandise"
    / "face-forward-parking-sign-12x18.webp"
)

PRODUCT_URLS = {
    "mug": "https://face-forward-shop.fourthwall.com/products/face-forward-black-glossy-mug",
    "t-shirt": "https://face-forward-shop.fourthwall.com/products/face-forward-t-shirt",
    "bumper sticker": "https://face-forward-shop.fourthwall.com/products/face-forward-bumper-sticker",
}


def test_store_lists_the_three_fourthwall_products_without_placeholder_copy():
    page = STORE_PAGE.read_text()

    for product_name, product_url in PRODUCT_URLS.items():
        assert product_url in page, f"Missing Fourthwall link for {product_name}"

    assert "Not a store. Not yet." not in page
    assert "Placeholder" not in page


def test_store_displays_the_parking_sign_as_a_not_for_sale_artifact():
    page = STORE_PAGE.read_text()

    assert SIGN_ASSET.exists()
    assert "/assets/merchandise/face-forward-parking-sign-12x18.webp" in page
    assert "Not for sale" in page
    assert "campaign object" in page.lower()
    assert "Face Forward Only parking sign" in page


def test_homepage_introduces_the_current_first_edition_collection():
    page = HOME_PAGE.read_text()

    assert "One T-shirt. One bumper sticker. One black glossy mug." in page
    assert "One bumper sticker. One wearable. One sign for the lot." not in page
    assert "Placeholder product rendering" not in page
    assert "View the First Edition" in page
