-- 单管理员网页登录账户。
-- 密码只保存为 PBKDF2-SHA256 哈希，初始化与改密由 scripts/manage_admin_user.py 完成。
-- user_id 固定为 1，数据库层保证仅存在一个管理员账户；不引入角色或权限模型。

CREATE TABLE admin_user (
    user_id       TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    singleton     TINYINT UNSIGNED NOT NULL DEFAULT 1,
    username      VARCHAR(128) NOT NULL,
    password_hash VARCHAR(512) NOT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_admin_user_singleton (singleton),
    UNIQUE KEY uk_admin_user_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易管理后台单管理员账户';
