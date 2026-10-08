.DEFAULT_GOAL := help
DC := docker compose
EXEC := $(DC) exec -T app

help:
	@echo "make setup   build images, start services, create schemas"
	@echo "make run     full pipeline: ingest batch 1 -> ELT -> batch 2 -> ELT -> marts"
	@echo "make test    pytest suite"
	@echo "make serve   answer the 5 business questions"
	@echo "make demo    setup + run + test + serve, end to end"
	@echo "make logs    pipeline_runs table"
	@echo "make psql    interactive shell on the warehouse"
	@echo "make clean   stop services and delete all data"

setup:
	$(DC) up -d --build
	@echo "waiting for postgres..."
	@$(DC) exec -T postgres sh -c 'until pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB; do sleep 1; done'
	$(EXEC) python -m pipeline.bootstrap

run:
	$(EXEC) python -m pipeline.run --full

test:
	$(EXEC) python -m pytest tests/ -v --tb=short

serve:
	$(EXEC) python -m pipeline.serve

demo: setup run test serve
	@echo ""
	@echo "capstone complete."

logs:
	$(EXEC) python -m pipeline.runlog

psql:
	$(DC) exec postgres psql -U $$(grep POSTGRES_USER .env | cut -d= -f2) -d $$(grep POSTGRES_DB .env | cut -d= -f2)

clean:
	$(DC) down -v

.PHONY: help setup run test serve demo logs psql clean

# --------------------------------------------------------------------
# Live demo: fresh run, incremental run, deliberate quality failure.
# --------------------------------------------------------------------
demo-live: clean setup
	@echo ""
	@echo "########## 1. FRESH RUN -- batch 1 only ##########"
	$(EXEC) python -m pipeline.run --max-ts 2018-01-01
	@echo ""
	@echo "########## 2. INCREMENTAL -- batch 2 arrives ##########"
	$(EXEC) python -m pipeline.run
	@echo ""
	@echo "########## 3. RE-RUN -- proves idempotency ##########"
	$(EXEC) python -m pipeline.run
	@$(MAKE) --no-print-directory demo-fail
	@echo ""
	@echo "########## 5. MARTS + BUSINESS ANSWERS ##########"
	$(EXEC) python -m pipeline.run --marts
	$(EXEC) python -m pipeline.serve
	@echo ""
	@echo "live demo complete."

demo-fail:
	@echo ""
	@echo "########## 4. QUALITY GATE FAILS ON PURPOSE ##########"
	@echo "-- warehouse before:"
	@$(EXEC) python -m pipeline.snapshot
	@echo "-- injecting a negative price into staging..."
	@$(DC) exec -T postgres psql -U olist -d olist_capstone -q -c \
	  "insert into staging.order_items (order_id, order_item_id, product_id, seller_id, price, freight_value) select order_id, 99, (select product_id from source.products limit 1), (select seller_id from source.sellers limit 1), -42.00, 0 from staging.orders limit 1;"
	@echo "-- running the pipeline (expected to FAIL):"
	@$(EXEC) python -m pipeline.run; \
	  rc=$$?; echo "   exit code = $$rc"; \
	  if [ $$rc -eq 0 ]; then echo "   *** GATE DID NOT FIRE ***"; exit 1; fi
	@echo "-- warehouse after the failed run (must be unchanged):"
	@$(EXEC) python -m pipeline.snapshot
	@echo "-- removing the bad row and recovering:"
	@$(DC) exec -T postgres psql -U olist -d olist_capstone -q -c \
	  "delete from staging.order_items where order_item_id = 99 and price < 0;"
	@$(EXEC) python -m pipeline.run | tail -4

.PHONY: demo-live demo-fail
