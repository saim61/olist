-- =====================================================================
-- Simulated operational system. Typed copy of the raw CSVs.
-- =====================================================================
drop table if exists source.order_reviews  cascade;
drop table if exists source.order_payments cascade;
drop table if exists source.order_items    cascade;
drop table if exists source.orders         cascade;
drop table if exists source.products       cascade;
drop table if exists source.sellers        cascade;
drop table if exists source.customers      cascade;
drop table if exists source.geolocation    cascade;
drop table if exists source.product_category_translation cascade;

create table source.product_category_translation (
    product_category_name         varchar(50) primary key,
    product_category_name_english varchar(50) not null
);

create table source.geolocation (
    geolocation_id              bigint generated always as identity primary key,
    geolocation_zip_code_prefix varchar(5)       not null,
    geolocation_lat             double precision not null,
    geolocation_lng             double precision not null,
    geolocation_city            varchar(50)      not null,
    geolocation_state           char(2)          not null
);

-- zip is varchar, not integer: 24% of prefixes carry a leading zero.
create table source.customers (
    customer_id              char(32)    primary key,
    customer_unique_id       char(32)    not null,
    customer_zip_code_prefix varchar(5)  not null,
    customer_city            varchar(50) not null,
    customer_state           char(2)     not null
);

create table source.sellers (
    seller_id              char(32)    primary key,
    seller_zip_code_prefix varchar(5)  not null,
    seller_city            varchar(50) not null,
    seller_state           char(2)     not null
);

-- No FK to the translation table: 2 categories have no translation row.
create table source.products (
    product_id                 char(32) primary key,
    product_category_name      varchar(50),
    product_name_lenght        integer,
    product_description_lenght integer,
    product_photos_qty         integer,
    product_weight_g           integer,
    product_length_cm          integer,
    product_height_cm          integer,
    product_width_cm           integer
);

create table source.orders (
    order_id                      char(32)    primary key,
    customer_id                   char(32)    not null references source.customers,
    order_status                  varchar(20) not null,
    order_purchase_timestamp      timestamp   not null,
    order_approved_at             timestamp,
    order_delivered_carrier_date  timestamp,
    order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp   not null,
    constraint ck_orders_status check (order_status in (
        'delivered','shipped','canceled','unavailable',
        'invoiced','processing','created','approved'))
);

create table source.order_items (
    order_id            char(32)      not null references source.orders,
    order_item_id       integer       not null,
    product_id          char(32)      not null references source.products,
    seller_id           char(32)      not null references source.sellers,
    shipping_limit_date timestamp,
    price               numeric(10,2) not null,
    freight_value       numeric(10,2) not null,
    primary key (order_id, order_item_id),
    constraint ck_items_money check (price >= 0 and freight_value >= 0)
);

create table source.order_payments (
    order_id             char(32)      not null references source.orders,
    payment_sequential   integer       not null,
    payment_type         varchar(20)   not null,
    payment_installments integer       not null,
    payment_value        numeric(10,2) not null,
    primary key (order_id, payment_sequential)
);

-- review_id alone is not unique.
create table source.order_reviews (
    review_id               char(32)  not null,
    order_id                char(32)  not null references source.orders,
    review_score            smallint  not null,
    review_comment_title    text,
    review_comment_message  text,
    review_creation_date    timestamp not null,
    review_answer_timestamp timestamp not null,
    primary key (review_id, order_id),
    constraint ck_review_score check (review_score between 1 and 5)
);

create index idx_src_orders_purchased on source.orders (order_purchase_timestamp);
create index idx_src_orders_customer  on source.orders (customer_id);
create index idx_src_items_product    on source.order_items (product_id);
create index idx_src_items_seller     on source.order_items (seller_id);
create index idx_src_cust_unique      on source.customers (customer_unique_id);
