import os
import logging
from typing import Optional, Callable
import ddddocr

# Configure lightweight logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CaptchaSolver")

# Initialize ddddocr model globally once to avoid re-loading weights on every call
try:
    _ocr_engine = ddddocr.DdddOcr(beta=True, show_ad=False)
except Exception:
    # Fallback initialization if beta mode fails on specific platforms
    _ocr_engine = ddddocr.DdddOcr(show_ad=False)


def solve_image_captcha(image_path: str, force_uppercase: bool = False) -> Optional[str]:
    """
    Reads a local CAPTCHA image file and uses ddddocr to predict the text.
    
    :param image_path: Path to the saved image file (e.g., 'temp_captcha.png')
    :param force_uppercase: Set to True if portal expects all uppercase characters.
    :return: Predicted string, or None if reading/processing failed.
    """
    if not os.path.exists(image_path):
        logger.error(f"Image file not found: {image_path}")
        return None

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # Perform local OCR classification
        prediction = _ocr_engine.classification(image_bytes)
        
        if not prediction:
            logger.warning("OCR returned an empty prediction.")
            return None

        # Clean whitespace/newlines
        clean_text = prediction.strip()

        # Handle portal case sensitivity preference
        if force_uppercase:
            clean_text = clean_text.upper()

        logger.info(f"OCR Prediction Successful: '{clean_text}'")
        return clean_text

    except Exception as e:
        logger.error(f"Error during OCR processing: {e}")
        return None


def execute_with_retry(
    get_captcha_file_fn: Callable[[], str],
    submit_form_fn: Callable[[str], bool],
    refresh_captcha_fn: Callable[[], None],
    max_retries: int = 3,
    force_uppercase: bool = False
) -> bool:
    """
    Generic execution harness for web automation scripts.
    Handles the loop: Capture -> Solve -> Submit -> (If Fail) Refresh & Retry.

    :param get_captcha_file_fn: Function that takes a screenshot of the CAPTCHA element and returns file path.
    :param submit_form_fn: Function that inputs the solved text, submits form, and returns True if successful.
    :param refresh_captcha_fn: Function that clicks the refresh CAPTCHA button on the page.
    :param max_retries: Maximum number of submission attempts (default: 3).
    :param force_uppercase: Pass True if portal forces uppercase text.
    :return: True if submission succeeded, False if retries exhausted.
    """
    for attempt in range(1, max_retries + 1):
        logger.info(f"CAPTCHA Solve Attempt {attempt}/{max_retries}...")

        # 1. Capture/save current image from DOM
        image_path = get_captcha_file_fn()

        # 2. Predict text locally
        solved_text = solve_image_captcha(image_path, force_uppercase=force_uppercase)

        if solved_text:
            # 3. Attempt form submission
            success = submit_form_fn(solved_text)
            if success:
                logger.info("Form submission verified successfully!")
                return True
            else:
                logger.warning("Form submission failed (Invalid CAPTCHA or submission error).")

        # 4. If reached here and retries remain, refresh image and retry
        if attempt < max_retries:
            logger.info("Refreshing CAPTCHA image for next attempt...")
            refresh_captcha_fn()

    logger.error("Exhausted all CAPTCHA retries.")
    return False