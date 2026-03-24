#!/usr/bin/env python3
"""
Extract DINOv2 features from cropped artwork images and store in ChromaDB.
Creates a separate collection for DINOv2 features (768 dim).
"""

import os
import torch
import chromadb
import numpy as np
from PIL import Image
from tqdm import tqdm
from transformers import AutoImageProcessor, AutoModel

# Config
ARTSENSE_DIR = "/Users/dacai/Projects/Artsense"
CROPPED_IMAGES_DIR = f"{ARTSENSE_DIR}/data/processed/moc/images"
CHROMA_DIR = f"{ARTSENSE_DIR}/data/chroma_public_art"
DINO_COLLECTION = "public_art_dino_features"  # Separate collection for DINOv2

# Load DINOv2
print("Loading DINOv2 model...")
processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
model = AutoModel.from_pretrained("facebook/dinov2-base")
model.eval()
print("Model loaded!\n")

def extract_features(image_path):
    """Extract DINOv2 CLS token features from an image."""
    img = Image.open(image_path).convert('RGB')
    img_resized = img.resize((224, 224))
    inputs = processor(images=img_resized, return_tensors="pt")
    
    with torch.no_grad():
        outputs = model(**inputs)
        embeddings = outputs.last_hidden_state[:, 0, :].numpy()
    
    return embeddings.flatten()

# Connect to ChromaDB
client = chromadb.PersistentClient(path=CHROMA_DIR)

# Create dedicated DINOv2 collection
try:
    dino_collection = client.get_collection(DINO_COLLECTION)
    print(f"Using existing collection '{DINO_COLLECTION}' ({dino_collection.count()} items)")
    dino_collection.delete(delete_all=True)
    print("Cleared existing data")
except:
    pass

dino_collection = client.create_collection(
    name=DINO_COLLECTION,
    metadata={
        "description": "DINOv2-base CLS token features (768 dim) for public artwork",
        "dimension": 768
    }
)
print(f"Created new collection '{DINO_COLLECTION}'\n")

# Get metadata from existing public_art_works collection
main_collection = client.get_collection("public_art_works")
all_items = main_collection.get()
print(f"Main collection has {main_collection.count()} items")

# Build ID mapping
id_to_meta = {}
for i, item_id in enumerate(all_items['ids']):
    id_to_meta[item_id] = all_items['metadatas'][i]

# Find cropped images and match to work IDs
print("\nExtracting DINOv2 features...")
updated = 0

for crop_file in sorted(os.listdir(CROPPED_IMAGES_DIR)):
    if not crop_file.endswith('_crop.jpg'):
        continue
    
    # Derive work_id: "01-躍龍門_crop.jpg" -> "work_11287"
    base_name = crop_file.replace('_crop.jpg', '')
    
    # Find matching work_id by original image file
    work_id = None
    for i, item_id in enumerate(all_items['ids']):
        orig = all_items['metadatas'][i].get('image_file', '')
        if orig.replace('.jpg', '') == base_name:
            work_id = item_id
            break
    
    if not work_id:
        print(f"  ⚠ No match for {crop_file}")
        continue
    
    # Extract features
    crop_path = os.path.join(CROPPED_IMAGES_DIR, crop_file)
    features = extract_features(crop_path)
    
    # Get metadata from main collection
    meta = id_to_meta.get(work_id, {}).copy()
    meta['cropped_file'] = crop_file
    meta['feature_model'] = 'dinov2-base'
    
    # Store in DINO collection
    dino_collection.add(
        ids=[work_id],
        embeddings=[features.tolist()],
        metadatas=[meta]
    )
    
    updated += 1
    print(f"  ✓ {work_id}: {meta.get('title', crop_file)}")

print(f"\n✅ Done! {updated} items stored in '{DINO_COLLECTION}'")
print(f"   Collection count: {dino_collection.count()}")
