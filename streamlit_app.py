import streamlit as st
import pandas as pd
from datetime import datetime
from streamlit_drawable_canvas import st_canvas
from sqlalchemy import text
import base64
from io import BytesIO
from PIL import Image

# --- 1. 页面配置 ---
st.set_page_config(page_title="美甲店管理系统", page_icon="💅")

# --- 2. 数据库连接 ---
# 这里会自动读取 .streamlit/secrets.toml 的配置
conn = st.connection("supabase", type="sql")

# --- 3. 辅助函数 ---

def run_query(query_str, params=None):
    """执行只读查询，返回 DataFrame"""
    if params is None:
        params = {}
    # 使用 st.cache_data? 不，对于实时性要求高的记账系统，直接查比较稳妥
    return conn.query(query_str, params=params, ttl=0)

def run_transaction(query_str, params):
    """执行增删改操作 (写入)"""
    with conn.session as s:
        s.execute(text(query_str), params)
        s.commit()

def process_signature(image_data):
    """处理签字图片"""
    if image_data is None:
        return None
    img = Image.fromarray(image_data.astype('uint8'), 'RGBA')
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

# --- 4. 界面逻辑 ---

st.title("💅 美甲店云端管理")

# 侧边栏导航
menu = st.sidebar.radio("功能菜单", ["消费结账 (含签字)", "会员充值", "新建会员", "会员查询/修改", "账目查询"])

# ==========================
# 功能 A: 新建会员
# ==========================
if menu == "新建会员":
    st.header("👤 录入新会员")
    with st.form("new_member_form"):
        name = st.text_input("姓名")
        phone = st.text_input("手机号 (作为唯一ID)")
        birthday = st.date_input("生日", value=datetime(2000, 1, 1),       # 默认停在 1990 年
            min_value=datetime(1950, 1, 1),   # 最早只能选到 1950
            max_value=datetime.now()          # 最晚只能选到今天
        )
        
        note = st.text_area("备注 (喜好/忌讳)")
        submitted = st.form_submit_button("创建会员")
        
        if submitted:
            try:
                # 1. 插入会员
                sql_member = """
                    INSERT INTO members (name, phone, birthday, note) 
                    VALUES (:name, :phone, :birthday, :note);
                """
                run_transaction(sql_member, {
                    "name": name, 
                    "phone": phone, 
                    "birthday": birthday, 
                    "note": note
                })

                # 2. 初始化账户 (需要先获取刚才生成的 id)
                # 在 Postgres 中，我们可以分开查，或者用 RETURNING，简单起见分开查
                member_df = run_query("SELECT id FROM members WHERE phone = :phone", {"phone": phone})
                if not member_df.empty:
                    m_id = int(member_df.iloc[0]['id'])
                    sql_account = "INSERT INTO accounts (member_id, balance, current_discount) VALUES (:mid, 0, 1.0);"
                    run_transaction(sql_account, {"mid": m_id})
                    st.success(f"会员 {name} 创建成功！")
                else:
                    st.error("创建失败，未找到新会员ID")
                    
            except Exception as e:
                st.error(f"发生错误 (可能是手机号重复): {e}")

# ==========================
# 功能: 会员查询/修改 (新增)
# ==========================
elif menu == "会员查询/修改":
    st.header("🔍 会员档案管理")
    phone_search = st.text_input("输入手机号查找会员", placeholder="例如: 13800138000")
    
    if phone_search:
        # 联合查询会员基本信息和账户信息
        sql = """
            SELECT m.id, m.name, m.phone, m.birthday, m.note, m.created_at,
                   a.balance, a.current_discount 
            FROM members m 
            LEFT JOIN accounts a ON m.id = a.member_id 
            WHERE m.phone = :phone
        """
        df = run_query(sql, {"phone": phone_search})
        
        if not df.empty:
            row = df.iloc[0]
            m_id = int(row['id'])
            m_note = row['note'] if row['note'] else ""
            
            # 1. 展示基本信息卡片
            st.success(f"已找到会员: **{row['name']}**")
            
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"📱 **手机:** {row['phone']}")
                st.write(f"🎂 **生日:** {row['birthday']}")
                # 格式化一下注册日期，只显示到天
                reg_date = pd.to_datetime(row['created_at']).strftime('%Y-%m-%d')
                st.caption(f"注册时间: {reg_date}")
            
            with col2:
                # 显示大字体的余额和折扣
                st.metric("当前余额", f"¥{row['balance']}")
                disc_display = f"{int(row['current_discount']*100)}折" if row['current_discount'] < 1.0 else "无折扣"
                st.metric("当前权益", disc_display)

            st.divider()
            
            # 2. 修改备注区域
            st.subheader("📝 修改备注")
            
            with st.form("edit_note_form"):
                # 文本框里默认填入从数据库查出来的旧备注
                new_note = st.text_area("备注内容 (喜好/忌讳/特别说明)", value=m_note, height=100)
                
                submit_update = st.form_submit_button("💾 保存备注修改")
                
                if submit_update:
                    try:
                        # 更新数据库
                        update_sql = "UPDATE members SET note = :note WHERE id = :mid"
                        run_transaction(update_sql, {"note": new_note, "mid": m_id})
                        
                        st.success("备注已更新！")
                        # 延迟刷新页面，让用户看到成功提示
                        import time
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"修改失败: {e}")
                        
        else:
            st.info("未找到该手机号，请检查输入。")

# ==========================
# 功能 B: 会员充值 (修改版)
# ==========================
elif menu == "会员充值":
    st.header("💰 会员充值")
    phone_search = st.text_input("输入手机号查找")
    
    if phone_search:
        # SQL 查询
        sql = """
            SELECT m.id, m.name, a.balance, a.current_discount 
            FROM members m 
            JOIN accounts a ON m.id = a.member_id 
            WHERE m.phone = :phone
        """
        df = run_query(sql, {"phone": phone_search})
        
        if not df.empty:
            row = df.iloc[0]
            m_id = int(row['id'])
            m_bal = float(row['balance'])
            m_disc = float(row['current_discount'])
            
            st.info(f"会员: **{row['name']}** | 当前余额: **¥{m_bal}** | 当前折扣: **{int(m_disc*100) if m_disc*100%10!=0 else int(m_disc*10)}折**")
            
            with st.form("recharge_form"):
                amount = st.number_input("充值金额", min_value=0.0, step=100.0)
                
                st.write("---")
                st.write("**折扣设置:**")
                
                # 1. 定义选项，加入 "自定义"
                option_list = [1.0, 0.95, 0.9, 0.88, 0.8, 0.7, 0.6, "自定义"]
                
                # 2. 选择框
                selected_option = st.selectbox(
                    "选择折扣等级", 
                    option_list, 
                    # 这里写了个复杂的表达式，是为了让 0.88 显示为 88折，0.9 显示为 9折
                    format_func=lambda x: x if x == "自定义" else ("原价" if x==1.0 else f"{int(x*100) if x*100%10!=0 else int(x*10)}折"),
                    # 默认选中当前的折扣
                    index=option_list.index(m_disc) if m_disc in option_list else 7 
                )
                
                # 3. 如果选了自定义，弹出一个输入框
                if selected_option == "自定义":
                    new_discount = st.number_input(
                        "手动输入折扣 (如: 0.85 代表85折)", 
                        min_value=0.0, 
                        max_value=1.0, 
                        value=m_disc, 
                        step=0.01,
                        format="%.2f"
                    )
                else:
                    new_discount = float(selected_option)

                # 提示文字
                if new_discount < 1.0:
                    st.caption(f"💡 确认: 将应用 **{int(new_discount*100)}折** (¥100 变 ¥{100*new_discount:.0f})")
                else:
                    st.caption("💡 确认: 恢复 **原价**")
                
                confirm = st.form_submit_button("确认充值")
                
                if confirm:
                    new_bal = m_bal + amount
                    # 更新账户
                    run_transaction(
                        "UPDATE accounts SET balance = :bal, current_discount = :disc WHERE member_id = :mid",
                        {"bal": new_bal, "disc": new_discount, "mid": m_id}
                    )
                    # 写入流水
                    run_transaction(
                        "INSERT INTO transactions (member_id, type, amount, detail, date) VALUES (:mid, 'RECHARGE', :amt, :detail, NOW())",
                        {"mid": m_id, "amt": amount, "detail": f"充值{amount}, 折扣变{new_discount:.2f}"}
                    )
                    st.success("充值成功！")
                    st.rerun()
        else:
            st.warning("查无此人")

# ==========================
# 功能 C: 消费结账 (含签字) - 修复交互版
# ==========================
elif menu == "消费结账 (含签字)":
    st.header("✍️ 消费确认")
    phone_search = st.text_input("输入手机号")
    
    if phone_search:
        # 查询会员
        sql = """
            SELECT m.id, m.name, a.balance, a.current_discount 
            FROM members m 
            JOIN accounts a ON m.id = a.member_id 
            WHERE m.phone = :phone
        """
        df = run_query(sql, {"phone": phone_search})
        
        if not df.empty:
            row = df.iloc[0]
            m_id = int(row['id'])
            m_bal = float(row['balance'])
            m_disc = float(row['current_discount'])
            
            # 显示卡片
            col1, col2, col3 = st.columns(3)
            col1.metric("会员姓名", row['name'])
            col2.metric("当前余额", f"¥{m_bal}")
            disc_display = f"{int(m_disc*100) if m_disc*100%10!=0 else int(m_disc*10)}折" if m_disc < 1.0 else "原价"
            col3.metric("当前权益", disc_display)
            
            st.divider()

            # --- 1. 选择项目 (注意：这一块必须在 form 外面，才能实时响应) ---
            st.subheader("1. 选择项目")
            
            MENU_DATA = {
                "🖐️ 手部": ["卸甲", "修补", "延长", "款式", "饰品"],
                "👁️ 睫毛": ["卸睫毛", "漫画款", "婴儿弯", "YY单根", "设计款", "蛋白矫正"],
                "🦶 足部": ["卸甲", "水晶矫正", "甲片", "款式", "足部护理"],
                "🤨 眉毛": ["野生眉", "线条眉", "雾眉", "洗眉"]
            }

            # 一级标题 (大类)
            selected_categories = st.multiselect(
                "请选择服务大类 (支持多选)",
                options=list(MENU_DATA.keys())
            )
            
            final_item_list = [] 
            validation_error = False 
            
            # 动态生成二级标题
            if selected_categories:
                st.write("👇 **请勾选具体细项:**")
                for cat in selected_categories:
                    sub_options = MENU_DATA[cat]
                    selected_subs = st.multiselect(
                        f"{cat} - 具体内容",
                        options=sub_options
                    )
                    
                    if not selected_subs:
                        st.caption(f"⚠️ 待选择: [{cat}] 细项...") # 用灰色文字提示，不报错干扰
                        validation_error = True
                    else:
                        cat_clean = cat.split(' ')[1] if ' ' in cat else cat
                        item_str = f"{cat_clean}({','.join(selected_subs)})"
                        final_item_list.append(item_str)
            
            # 手动备注
            other_note = st.text_input("补充说明/其他项目", placeholder="例如: 加钻, 纯色...")
            if other_note:
                final_item_list.append(f"备注[{other_note}]")

            # 拼接最终字符串
            final_detail_string = " + ".join(final_item_list)

            # 如果有选内容，实时显示一个预览条
            if final_detail_string:
                st.info(f"🛒 已选: {final_detail_string}")
            
            st.write("---")

            # --- 2. 结算确认表单 (这部分放进 form，防止误触提交) ---
            with st.form("pay_form"):
                st.subheader("2. 确认金额与签字")
                
                price = st.number_input("订单原价总额", min_value=0.0, step=10.0)
                final_price = price * m_disc
                
                st.markdown(f"### 应扣款: <span style='color:red'>¥{final_price:.2f}</span> (折扣: {disc_display})", unsafe_allow_html=True)
                
                st.write("请顾客签字 👇")
                canvas_result = st_canvas(
                    fill_color="rgba(255, 165, 0, 0.3)",
                    stroke_width=2,
                    stroke_color="#000000",
                    background_color="#EEE",
                    height=150,
                    drawing_mode="freedraw",
                    key="canvas_spend",
                )
                
                submit = st.form_submit_button("✅ 确认扣款", type="primary")
                
                if submit:
                    # 校验
                    if not final_item_list and not other_note:
                         st.warning("❌ 请至少选择一项服务或填写备注！")
                         st.stop()

                    if validation_error:
                        st.warning("❌ 请将已选大类的具体细项补充完整！")
                        st.stop()

                    if m_bal >= final_price:
                        # 处理签字
                        sig_str = ""
                        if canvas_result.image_data is not None:
                            sig_str = process_signature(canvas_result.image_data)
                        
                        # 扣款
                        run_transaction(
                            "UPDATE accounts SET balance = :bal WHERE member_id = :mid",
                            {"bal": m_bal - final_price, "mid": m_id}
                        )
                        # 记账
                        run_transaction(
                            """INSERT INTO transactions (member_id, type, amount, detail, date, signature) 
                               VALUES (:mid, 'SPEND', :amt, :detail, NOW(), :sig)""",
                            {"mid": m_id, "amt": final_price, "detail": final_detail_string, "sig": sig_str}
                        )
                        st.balloons()
                        st.success("交易成功！")
                        import time
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ 余额不足！")
        else:
            st.warning("未找到该会员")
# ==========================
# 功能 D: 账目查询
# ==========================
elif menu == "账目查询":
    st.header("📊 最近交易")
    
    # 简单的 SQL 报表
    sql = """
        SELECT t.date as 时间, m.name as 姓名, t.type as 类型, t.amount as 金额, t.detail as 详情, t.signature
        FROM transactions t
        JOIN members m ON t.member_id = m.id
        ORDER BY t.id DESC
        LIMIT 20
    """
    df = run_query(sql)
    
    if not df.empty:
        for i, row in df.iterrows():
            with st.expander(f"{row['时间']} - {row['姓名']} - {row['类型']} ¥{row['金额']}"):
                st.write(f"详情: {row['详情']}")
                if row['signature']:
                    try:
                        # 还原图片
                        img_bytes = base64.b64decode(row['signature'])
                        st.image(img_bytes, caption="顾客签字", width=200)
                    except:
                        st.text("图片加载失败")
    else:
        st.info("暂无数据")