import streamlit as st
from sqlalchemy import text

# 1. 建立连接
# Streamlit 会自动读取 .streamlit/secrets.toml 里的配置
conn = st.connection("supabase", type="sql")

print("正在尝试连接 Supabase...")

try:
    # 2. 查询数据 (注意：SQLAlchemy 需要用 text() 包裹 SQL 语句)
    # session.execute 返回的是 ResultProxy，需要 .fetchall()
    rows = conn.query("SELECT * FROM members;", ttl=0)
    
    print("✅ 连接成功！读取到的数据如下：")
    print(rows)
    
    # 3. 尝试写入数据 (测试用)
    # 使用 with conn.session as s: 来自动提交事务
    with conn.session as s:
        # 生成一个随机手机号避免唯一性冲突
        import random
        rand_phone = f"139{random.randint(10000000, 99999999)}"
        
        sql = text("INSERT INTO members (name, phone, note) VALUES (:name, :phone, :note)")
        s.execute(sql, {"name": "Python脚本", "phone": rand_phone, "note": "自动写入测试"})
        s.commit()
        
    print(f"✅ 写入测试成功！插入了手机号 {rand_phone}")

except Exception as e:
    print("❌ 出错啦:", e)