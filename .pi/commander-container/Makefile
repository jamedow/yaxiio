.PHONY: build start stop restart logs clean test shell

# ── 默认配置 ──
REDIS_PASSWORD ?= commander-secret
DASHBOARD_PORT ?= 3003

export REDIS_PASSWORD
export DASHBOARD_PORT

# ── Docker 命令 ──

build:
	docker compose build

start:
	docker compose up -d

stop:
	docker compose down

restart: stop start

logs:
	docker compose logs -f --tail=100

shell:
	docker compose exec commander /entrypoint.sh shell

clean:
	docker compose down -v
	rm -rf logs/ data/

# ── 开发命令 ──

dev-setup:
	pip install -r requirements.txt
	npm install -g pm2

dev-redis:
	docker run -d --name commander-redis-dev \
		-p 6379:6379 \
		redis:7-alpine \
		redis-server --requirepass $(REDIS_PASSWORD)

dev-start:
	cd src && python3 commander_v2.py

dev-dashboard:
	cd agents && python3 dashboard_v2.py

dev-tui:
	cd agents && python3 commander-tui.py

# ── 测试 ──

test:
	@echo "=== 语法检查 ==="
	python3 -m py_compile src/*.py
	python3 -m py_compile agents/*.py
	@echo "✅ 所有文件编译通过"

test-integration:
	cd src && python3 -c "
from skill_manager import SkillManager, LocalSkillAdapter
from mcp_manager import MCPManager, MCPBootstrap
from extension_router import ExtensionRouter, build_extension_router
print('✅ 所有模块导入成功')
"

# ── 开源准备 ──

check-headers:
	@echo "=== 检查 Apache 2.0 许可证头 ==="
	@missing=0; \
	for f in $$(find src agents -name '*.py' -o -name '*.sh'); do \
		if ! grep -q 'Apache-2.0' "$$f" 2>/dev/null; then \
			echo "❌ 缺少许可证头: $$f"; \
			missing=$$((missing+1)); \
		fi; \
	done; \
	if [ $$missing -eq 0 ]; then echo "✅ 所有文件已包含许可证头"; fi

archive:
	tar -czf commander-v$$(cat VERSION).tar.gz \
		--exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
		src/ agents/ scripts/ docs/ \
		LICENSE NOTICE VERSION README.md CHANGELOG.md \
		requirements.txt Dockerfile docker-compose.yml Makefile
	@echo "📦 归档完成: commander-v$$(cat VERSION).tar.gz"
