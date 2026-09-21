"""命令行工具：初始化数据库、创建家庭与家庭令牌。

用法：
    python -m app.cli init-db
    python -m app.cli create-family --name "我的家" --role publisher
    python -m app.cli family-token --family-id <uuid> [--role executor]
"""

import argparse

from . import auth, db
from .services import stats_service


def main() -> None:
    parser = argparse.ArgumentParser(description="《待会儿办》M1 管理命令")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="建表（幂等）")

    p_create = sub.add_parser("create-family", help="新建家庭并签发家庭令牌")
    p_create.add_argument("--name", default="我的家")
    p_create.add_argument("--role", default="executor", choices=["publisher", "executor"])

    p_token = sub.add_parser("family-token", help="为已有家庭再签一条家庭令牌")
    p_token.add_argument("--family-id", required=True)
    p_token.add_argument("--role", default="executor", choices=["publisher", "executor"])

    args = parser.parse_args()

    if args.command == "init-db":
        db.init_db()
        print(f"已建表：{db.SCHEMA_FILE}")
        return

    db.init_db()
    with db.get_conn() as conn:
        if args.command == "create-family":
            family_id = auth.create_family(conn, args.name)
            token = auth.create_family_token(conn, family_id, role=args.role)
            print(f"family_id={family_id}")
            print(f"family_token={token}")
            print("请把令牌填进加入链接：https://<域名>/join?t=<family_token>")
        else:
            token = auth.create_family_token(conn, args.family_id, role=args.role)
            print(f"family_token={token}")
        stats_service.rollup(conn)


if __name__ == "__main__":
    main()
