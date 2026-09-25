# Olist E-Commerce Data Engineering

Data engineering project built on the Brazilian E-Commerce Public Dataset by Olist.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

## Getting the data

The CSVs are **not** tracked in this repository — they total roughly 48 MB and
are freely redistributable from the source. Download them yourself:

1. Get the dataset from Kaggle:
   <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
2. Unzip the nine CSV files into `data/olist/` so the tree looks like:

```
data/olist/
├── olist_customers_dataset.csv
├── olist_geolocation_dataset.csv
├── olist_order_items_dataset.csv
├── olist_order_payments_dataset.csv
├── olist_order_reviews_dataset.csv
├── olist_orders_dataset.csv
├── olist_products_dataset.csv
├── olist_sellers_dataset.csv
└── product_category_name_translation.csv
```

Or with the Kaggle CLI:

```bash
pip install kaggle
kaggle datasets download -d olistbr/brazilian-ecommerce -p data/olist --unzip
```
