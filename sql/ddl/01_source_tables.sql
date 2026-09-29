DROP TABLE IF EXISTS source.order_reviews  CASCADE;
DROP TABLE IF EXISTS source.order_payments CASCADE;
DROP TABLE IF EXISTS source.order_items    CASCADE;
DROP TABLE IF EXISTS source.orders         CASCADE;
DROP TABLE IF EXISTS source.products       CASCADE;
DROP TABLE IF EXISTS source.sellers        CASCADE;
DROP TABLE IF EXISTS source.customers      CASCADE;
DROP TABLE IF EXISTS source.geolocation    CASCADE;
DROP TABLE IF EXISTS source.product_category_translation CASCADE;

CREATE TABLE source.product_category_translation (
    product_category_name         VARCHAR(50) PRIMARY KEY,
    product_category_name_english VARCHAR(50) NOT NULL
);

CREATE TABLE source.geolocation (
    geolocation_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    geolocation_zip_code_prefix VARCHAR(5)       NOT NULL,
    geolocation_lat             DOUBLE PRECISION NOT NULL,
    geolocation_lng             DOUBLE PRECISION NOT NULL,
    geolocation_city            VARCHAR(50)      NOT NULL,
    geolocation_state           CHAR(2)          NOT NULL
);

CREATE TABLE source.customers (
    customer_id              CHAR(32)    PRIMARY KEY,
    customer_unique_id       CHAR(32)    NOT NULL,
    customer_zip_code_prefix VARCHAR(5)  NOT NULL,
    customer_city            VARCHAR(50) NOT NULL,
    customer_state           CHAR(2)     NOT NULL
);

CREATE TABLE source.sellers (
    seller_id              CHAR(32)    PRIMARY KEY,
    seller_zip_code_prefix VARCHAR(5)  NOT NULL,
    seller_city            VARCHAR(50) NOT NULL,
    seller_state           CHAR(2)     NOT NULL
);

CREATE TABLE source.products (
    product_id                 CHAR(32) PRIMARY KEY,
    product_category_name      VARCHAR(50),
    product_name_lenght        INTEGER,
    product_description_lenght INTEGER,
    product_photos_qty         INTEGER,
    product_weight_g           INTEGER,
    product_length_cm          INTEGER,
    product_height_cm          INTEGER,
    product_width_cm           INTEGER,
    CONSTRAINT ck_products_dims CHECK (
        (product_weight_g  IS NULL OR product_weight_g  >= 0) AND
        (product_length_cm IS NULL OR product_length_cm >= 0) AND
        (product_height_cm IS NULL OR product_height_cm >= 0) AND
        (product_width_cm  IS NULL OR product_width_cm  >= 0)
    )
);

CREATE TABLE source.orders (
    order_id                      CHAR(32)    PRIMARY KEY,
    customer_id                   CHAR(32)    NOT NULL,
    order_status                  VARCHAR(20) NOT NULL,
    order_purchase_timestamp      TIMESTAMP   NOT NULL,
    order_approved_at             TIMESTAMP,
    order_delivered_carrier_date  TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP   NOT NULL,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES source.customers (customer_id),
    CONSTRAINT ck_orders_status CHECK (order_status IN (
        'delivered', 'shipped', 'canceled', 'unavailable',
        'invoiced', 'processing', 'created', 'approved'))
);

CREATE TABLE source.order_items (
    order_id            CHAR(32)      NOT NULL,
    order_item_id       INTEGER       NOT NULL,
    product_id          CHAR(32)      NOT NULL,
    seller_id           CHAR(32)      NOT NULL,
    shipping_limit_date TIMESTAMP     NOT NULL,
    price               NUMERIC(10,2) NOT NULL,
    freight_value       NUMERIC(10,2) NOT NULL,
    CONSTRAINT pk_order_items PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_items_order   FOREIGN KEY (order_id)
        REFERENCES source.orders (order_id),
    CONSTRAINT fk_items_product FOREIGN KEY (product_id)
        REFERENCES source.products (product_id),
    CONSTRAINT fk_items_seller  FOREIGN KEY (seller_id)
        REFERENCES source.sellers (seller_id),
    CONSTRAINT ck_items_seq   CHECK (order_item_id >= 1),
    CONSTRAINT ck_items_money CHECK (price >= 0 AND freight_value >= 0)
);

CREATE TABLE source.order_payments (
    order_id             CHAR(32)      NOT NULL,
    payment_sequential   INTEGER       NOT NULL,
    payment_type         VARCHAR(20)   NOT NULL,
    payment_installments INTEGER       NOT NULL,
    payment_value        NUMERIC(10,2) NOT NULL,
    CONSTRAINT pk_order_payments PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id)
        REFERENCES source.orders (order_id),
    CONSTRAINT ck_payment_type CHECK (payment_type IN (
        'credit_card', 'boleto', 'voucher', 'debit_card', 'not_defined')),
    CONSTRAINT ck_payment_installments CHECK (payment_installments >= 0),
    CONSTRAINT ck_payment_value CHECK (payment_value >= 0)
);

CREATE TABLE source.order_reviews (
    review_id               CHAR(32)  NOT NULL,
    order_id                CHAR(32)  NOT NULL,
    review_score            SMALLINT  NOT NULL,
    review_comment_title    TEXT,
    review_comment_message  TEXT,
    review_creation_date    TIMESTAMP NOT NULL,
    review_answer_timestamp TIMESTAMP NOT NULL,
    CONSTRAINT pk_order_reviews PRIMARY KEY (review_id, order_id),
    CONSTRAINT fk_reviews_order FOREIGN KEY (order_id)
        REFERENCES source.orders (order_id),
    CONSTRAINT ck_review_score CHECK (review_score BETWEEN 1 AND 5)
);

CREATE INDEX idx_orders_customer   ON source.orders (customer_id);
CREATE INDEX idx_orders_purchased  ON source.orders (order_purchase_timestamp);
CREATE INDEX idx_orders_status     ON source.orders (order_status);
CREATE INDEX idx_items_product     ON source.order_items (product_id);
CREATE INDEX idx_items_seller      ON source.order_items (seller_id);
CREATE INDEX idx_payments_order    ON source.order_payments (order_id);
CREATE INDEX idx_reviews_order     ON source.order_reviews (order_id);
CREATE INDEX idx_customers_unique  ON source.customers (customer_unique_id);
CREATE INDEX idx_customers_zip     ON source.customers (customer_zip_code_prefix);
CREATE INDEX idx_sellers_zip       ON source.sellers (seller_zip_code_prefix);
CREATE INDEX idx_geolocation_zip   ON source.geolocation (geolocation_zip_code_prefix);
CREATE INDEX idx_products_category ON source.products (product_category_name);
