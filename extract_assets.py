#!/usr/bin/env python3
"""
Extract & Slice Playing Cards and Chip Assets from Poker cards 1.3.zip
======================================================================
Slices the 48x64 sprite sheet into individual PNGs:
  - 52 Playing Cards: <rank><suit>.png (e.g. Ah.png, Kd.png, Ts.png, 2c.png)
  - Card Backs: back_0.png through back_7.png
  - Poker Chips: sliced from fiches addon into web_gui/static/chips/
"""

import io
import os
import zipfile
from PIL import Image

def extract_assets(zip_path="Poker cards 1.3.zip", output_base="web_gui/static"):
    cards_dir = os.path.join(output_base, "cards")
    chips_dir = os.path.join(output_base, "chips")
    os.makedirs(cards_dir, exist_ok=True)
    os.makedirs(chips_dir, exist_ok=True)

    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Asset pack not found at {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as z:
        # 1. Extract Cards from "1.2 Poker cards.png"
        card_sheet_name = "Poker cards 1.3/1.2 Poker cards.png"
        deck_data = z.read(card_sheet_name)
        deck_img = Image.open(io.BytesIO(deck_data)).convert("RGBA")

        CW, CH = 48, 64
        # Standard layout:
        # Row 0: Hearts (h)
        # Row 1: Diamonds (d)
        # Row 2: Spades (s)
        # Row 3: Clubs (c)
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]
        suits = ["h", "d", "s", "c"]

        extracted_cards = []
        for row_idx, suit in enumerate(suits):
            for col_idx, rank in enumerate(ranks):
                crop = deck_img.crop((col_idx * CW, row_idx * CH, (col_idx + 1) * CW, (row_idx + 1) * CH))
                card_filename = f"{rank}{suit}.png"
                out_path = os.path.join(cards_dir, card_filename)
                crop.save(out_path, "PNG")
                extracted_cards.append(card_filename)

        # Jokers
        joker_red = deck_img.crop((13 * CW, 0 * CH, 14 * CW, 1 * CH))
        joker_red.save(os.path.join(cards_dir, "joker_red.png"), "PNG")
        joker_black = deck_img.crop((14 * CW, 0 * CH, 15 * CW, 1 * CH))
        joker_black.save(os.path.join(cards_dir, "joker_black.png"), "PNG")

        # Card Backs (Row 4)
        for col_idx in range(8):
            back_crop = deck_img.crop((col_idx * CW, 4 * CH, (col_idx + 1) * CW, 5 * CH))
            if back_crop.getbbox():
                back_crop.save(os.path.join(cards_dir, f"back_{col_idx}.png"), "PNG")
        
        # Default back copy
        default_back = os.path.join(cards_dir, "back_0.png")
        if os.path.exists(default_back):
            Image.open(default_back).save(os.path.join(cards_dir, "back.png"), "PNG")

        print(f"[+] Extracted {len(extracted_cards)} playing cards and backs into '{cards_dir}'")

        # 2. Extract Chips from "fiches addon (Poker Cards).png"
        fiches_name = "Poker cards 1.3/fiches addon (Poker Cards).png"
        if fiches_name in z.namelist():
            fiches_data = z.read(fiches_name)
            fiches_img = Image.open(io.BytesIO(fiches_data)).convert("RGBA")
            # Save raw sheet for CSS sprite usage
            fiches_img.save(os.path.join(chips_dir, "chips_sheet.png"), "PNG")
            
            # Slice distinct individual chip stacks (grid ~32x32 or 48x48)
            chip_colors = ["red", "blue", "green", "black", "yellow", "purple", "cyan", "white"]
            for idx, color in enumerate(chip_colors):
                x = idx * 32
                if x + 32 <= fiches_img.width:
                    chip_crop = fiches_img.crop((x, 0, x + 32, 32))
                    if chip_crop.getbbox():
                        chip_crop.save(os.path.join(chips_dir, f"chip_{color}.png"), "PNG")

            print(f"[+] Extracted chip sprites into '{chips_dir}'")

if __name__ == "__main__":
    extract_assets()
