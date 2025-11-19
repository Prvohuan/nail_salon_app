import streamlit as st
import pandas as pd
from datetime import datetime
from streamlit_drawable_canvas import st_canvas
from sqlalchemy import text
import base64
from io import BytesIO
from PIL import Image
import time

# --- 1. 页面配置 ---
st.set_page_config(page_title="美甲店SaaS系统", page_icon="💅")

# --- 2. 数据库连接 ---
conn = st.connection("supabase", type="sql")

# --- 3. 辅助函数 ---
def run_query(query_str, params=None):
    if params is None: params = {}
    return conn.query(query_str, params=params, ttl=0)

def run_transaction(query_str, params):
    with conn.session as s:
        s.execute(text(query_str), params)
        s.commit()

def process_signature(image_data):
    if image_data is None: return None
    img = Image.fromarray(image_data.astype('uint8'), 'RGBA')
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

# ===================================
# 🔐 多用户登录逻辑 (关键修改)
# ===================================
if "current_user" not in st.session_state:
    st.session_state.current_user = None
if "shop_name" not in st.session_state:
    st.session_state.shop_name = ""

def check_login():
    if st.session_state.current_user:
        return True
    
    st.header("🔐 美甲店管家 - 商家登录")
    with st.form("login_form"):
        # 这里输入你在 Supabase 插入的 username (比如 amy) 和 password
        username = st.text_input("商家账号")
        password = st.text_input("密码", type="password")
        submit = st.form_submit_button("登录")
        
        if submit:
            # 查询 shop_owners 表
            try:
                sql = "SELECT * FROM shop_owners WHERE username = :u AND password = :p"
                df = run_query(sql, {"u": username, "p": password})
                
                if not df.empty:
                    st.session_state.current_user = username
                    st.session_state.shop_name = df.iloc[0]['shop_name']
                    st.success("登录成功！")
                    st.rerun()
                else:
                    st.error("账号或密码错误")
            except Exception as e:
                st.error(f"数据库连接失败，请检查是否已创建 shop_owners 表。错误: {e}")
    return False

if not check_login():
    st.stop() # 未登录则停止运行

# 获取当前老板是谁，后续所有SQL都要用到它！
CURRENT_USER = st.session_state.current_user
SHOP_NAME = st.session_state.shop_name

# ===================================
# 💅 主程序开始
# ===================================

st.sidebar.title(f"🏠 {SHOP_NAME}")
st.sidebar.write(f"当前用户: {CURRENT_USER}")
if st.sidebar.button("退出登录"):
    st.session_state.current_user = None
    st.rerun()

menu = st.sidebar.radio("功能菜单", ["消费结账", "会员充值", "新建会员", "会员查询/修改", "账目查询"])
st.title(f"💅 {menu}")

# ==========================
# 功能 A: 新建会员
# ==========================
if menu == "新建会员":
    with st.form("new_member_form"):
        name = st.text_input("姓名")
        phone = st.text_input("手机号 (作为唯一ID)")
        birthday = st.date_input("生日", value=datetime(1995, 1, 1), min_value=datetime(1900, 1, 1))
        note = st.text_area("备注")
        submitted = st.form_submit_button("创建会员")
        
        if submitted:
            try:
                # 【关键修改】插入时加上 owner_username
                sql_member = """
                    INSERT INTO members (name, phone, birthday, note, owner_username) 
                    VALUES (:name, :phone, :birthday, :note, :owner);
                """
                run_transaction(sql_member, {
                    "name": name, "phone": phone, 
                    "birthday": birthday, "note": note,
                    "owner": CURRENT_USER # 👈 标记这条数据属于当前老板
                })

                # 初始化账户 (查刚才插入的ID)
                # 【关键修改】查询时也要限制 owner，防止手机号跨店冲突
                df = run_query("SELECT id FROM members WHERE phone = :phone AND owner_username = :owner", 
                               {"phone": phone, "owner": CURRENT_USER})
                
                if not df.empty:
                    m_id = int(df.iloc[0]['id'])
                    run_transaction("INSERT INTO accounts (member_id, balance, current_discount) VALUES (:mid, 0, 1.0)", {"mid": m_id})
                    st.success(f"会员 {name} 创建成功！")
                
            except Exception as e:
                st.error(f"创建失败 (可能手机号已存在): {e}")

# ==========================
# 功能 B: 会员充值 (支持姓名/尾号)
# ==========================
elif menu == "会员充值":
    search_term = st.text_input("搜索会员 (支持: 姓名 / 手机全号 / 手机后4位)").strip()
    
    if search_term:
        # 智能构造 SQL：支持 手机全号 OR 姓名 OR 手机尾号
        # 注意：Postgres 的 text 类型默认区分大小写，这里暂时不做忽略大小写处理，假设输入准确
        sql = """
            SELECT m.id, m.name, a.balance, a.current_discount 
            FROM members m 
            JOIN accounts a ON m.id = a.member_id 
            WHERE (m.phone = :term OR m.name = :term OR m.phone LIKE :tail)
            AND m.owner_username = :owner
        """
        # 如果输入是4位数字，就当作尾号处理 (在前面加 %)，否则尾号匹配项就填个不存在的值避免误伤
        tail_param = f"%{search_term}" if (len(search_term) == 4 and search_term.isdigit()) else "impossible_match"
        
        df = run_query(sql, {"term": search_term, "tail": tail_param, "owner": CURRENT_USER})
        
        if not df.empty:
            # 如果搜名字可能出现重名，这里默认取第一个。实际商用建议加个列表选择。
            if len(df) > 1:
                st.warning(f"⚠️ 找到 {len(df)} 个匹配项，默认显示第一个。建议使用手机号精准查找。")
            
            row = df.iloc[0]
            m_id, m_name, m_bal, m_disc = int(row['id']), row['name'], float(row['balance']), float(row['current_discount'])
            
            st.info(f"会员: **{m_name}** | 余额: **¥{m_bal}** | 折扣: **{int(m_disc*100) if m_disc<1 else '无'}**")
            
            with st.form("recharge_form"):
                amount = st.number_input("充值金额", step=100.0)
                
                st.write("**折扣设置:**")
                option_list = [1.0, 0.95, 0.9, 0.88, 0.8, 0.7, 0.6, "自定义"]
                selected_option = st.selectbox("选择折扣", option_list, 
                                            format_func=lambda x: x if x == "自定义" else ("原价" if x==1.0 else f"{int(x*100) if x*100%10!=0 else int(x*10)}折"),
                                            index=option_list.index(m_disc) if m_disc in option_list else 7)
                
                if selected_option == "自定义":
                    new_discount = st.number_input("输入折扣 (如0.85)", min_value=0.0, max_value=1.0, value=m_disc, step=0.01)
                else:
                    new_discount = float(selected_option)

                confirm = st.form_submit_button("确认充值")
                
                if confirm:
                    new_bal = m_bal + amount
                    run_transaction("UPDATE accounts SET balance = :bal, current_discount = :disc WHERE member_id = :mid",
                                    {"bal": new_bal, "disc": new_discount, "mid": m_id})
                    run_transaction(
                        """INSERT INTO transactions (member_id, type, amount, detail, date, owner_username) 
                           VALUES (:mid, 'RECHARGE', :amt, :detail, NOW(), :owner)""",
                        {"mid": m_id, "amt": amount, "detail": f"充值{amount}, 折扣变{new_discount:.2f}", "owner": CURRENT_USER}
                    )
                    st.success("充值成功！")
                    time.sleep(1)
                    st.rerun()
        else:
            st.warning("未找到会员")
            
# ==========================
# 功能 C: 消费结账 (实时计算 + 模糊搜索)
# ==========================
elif menu == "消费结账":
    search_term = st.text_input("搜索会员 (姓名 / 手机全号 / 尾号4位)").strip()
    
    if search_term:
        # 同样的模糊搜索逻辑
        sql = """
            SELECT m.id, m.name, a.balance, a.current_discount 
            FROM members m 
            JOIN accounts a ON m.id = a.member_id 
            WHERE (m.phone = :term OR m.name = :term OR m.phone LIKE :tail)
            AND m.owner_username = :owner
        """
        tail_param = f"%{search_term}" if (len(search_term) == 4 and search_term.isdigit()) else "impossible_match"
        df = run_query(sql, {"term": search_term, "tail": tail_param, "owner": CURRENT_USER})
        
        if not df.empty:
            row = df.iloc[0]
            m_id, m_name, m_bal, m_disc = int(row['id']), row['name'], float(row['balance']), float(row['current_discount'])
            
            col1, col2, col3 = st.columns(3)
            col1.metric("会员", m_name)
            col2.metric("余额", f"¥{m_bal}")
            col3.metric("权益", f"{int(m_disc*100)}折" if m_disc < 1 else "原价")
            st.divider()

            # --- 1. 选择项目 (保持不变) ---
            MENU_DATA = {
                "🖐️ 手部": ["卸甲", "修补", "延长", "款式", "饰品"],
                "👁️ 睫毛": ["卸睫毛", "漫画款", "婴儿弯", "YY单根", "设计款", "蛋白矫正"],
                "🦶 足部": ["卸甲", "水晶矫正", "甲片", "款式", "足部护理"],
                "🤨 眉毛": ["野生眉", "线条眉", "雾眉", "洗眉"]
            }

            st.subheader("1. 选择项目")
            selected_categories = st.multiselect("服务大类", options=list(MENU_DATA.keys()))
            final_item_list = []
            if selected_categories:
                st.write("👇 **勾选细项:**")
                for cat in selected_categories:
                    sub_options = MENU_DATA[cat]
                    selected_subs = st.multiselect(f"{cat} - 内容", options=sub_options)
                    if selected_subs:
                        cat_clean = cat.split(' ')[1] if ' ' in cat else cat
                        final_item_list.append(f"{cat_clean}({','.join(selected_subs)})")
            
            other_note = st.text_input("补充说明")
            if other_note: final_item_list.append(f"备注[{other_note}]")
            
            final_detail_string = " + ".join(final_item_list)
            if final_detail_string: st.info(f"🛒 已选: {final_detail_string}")
            st.write("---")

            # --- 2. 金额确认 (重点修改区域) ---
            st.subheader("2. 确认金额")
            
            # ⚠️ 移出 form，实现实时计算
            price = st.number_input("订单原价 (输入后回车)", min_value=0.0, step=10.0)
            final_price = price * m_disc
            
            # 实时显示大红字价格
            st.markdown(f"### 应扣款: <span style='color:red'>¥{final_price:.2f}</span>", unsafe_allow_html=True)

            # --- 3. 签字提交 (放进 form 防止误触) ---
            with st.form("pay_form"):
                st.write("请顾客签字 👇")
                canvas_result = st_canvas(fill_color="rgba(255, 165, 0, 0.3)", stroke_width=2, background_color="#EEE", height=150, key="canvas_spend")
                
                submit = st.form_submit_button("✅ 确认扣款", type="primary")
                
                if submit:
                    if not final_item_list and not other_note:
                         st.warning("❌ 请至少选择一项")
                         st.stop()
                    
                    if m_bal >= final_price:
                        sig_str = process_signature(canvas_result.image_data) if canvas_result.image_data is not None else ""
                        
                        run_transaction("UPDATE accounts SET balance = :bal WHERE member_id = :mid",
                                        {"bal": m_bal - final_price, "mid": m_id})
                        
                        run_transaction(
                            """INSERT INTO transactions (member_id, type, amount, detail, date, signature, owner_username) 
                               VALUES (:mid, 'SPEND', :amt, :detail, NOW(), :sig, :owner)""",
                            {"mid": m_id, "amt": final_price, "detail": final_detail_string, "sig": sig_str, "owner": CURRENT_USER}
                        )
                        st.balloons()
                        st.success("交易成功！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("余额不足")
        else:
            st.warning("未找到会员 (请尝试全号、尾号或姓名)")

# ==========================
# 功能: 会员查询/修改 (花名册模式)
# ==========================
elif menu == "会员查询/修改":
    st.header("🔍 会员档案管理")
    
    # 1. 搜索框
    search_term = st.text_input("搜索会员 (支持姓名/全号/尾号)", placeholder="留空则显示全部会员").strip()
    
    # 2. 构造 SQL (默认查所有，有搜索词则加筛选)
    sql = """
        SELECT m.id, m.name, m.phone, m.birthday, m.note, m.created_at, 
               a.balance, a.current_discount 
        FROM members m 
        LEFT JOIN accounts a ON m.id = a.member_id 
        WHERE m.owner_username = :owner
    """
    params = {"owner": CURRENT_USER}
    
    if search_term:
        sql += " AND (m.phone = :term OR m.name = :term OR m.phone LIKE :tail)"
        params["term"] = search_term
        # 如果是4位数字，当做尾号处理
        params["tail"] = f"%{search_term}" if (len(search_term)==4 and search_term.isdigit()) else "impossible_match"
    
    # 按注册时间倒序排列 (新的在前面)
    sql += " ORDER BY m.id DESC"
    
    df = run_query(sql, params)
    
    # 3. 界面展示逻辑
    if df.empty:
        st.info("暂无数据")
    else:
        # --- 情况 A: 刚好锁定 1 个人 -> 显示详情卡片 + 修改界面 ---
        if len(df) == 1:
            row = df.iloc[0]
            m_id = int(row['id'])
            
            st.success(f"已锁定会员: **{row['name']}**")
            
            # 详情卡片
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"📱 **手机:** {row['phone']}")
                st.write(f"🎂 **生日:** {row['birthday']}")
                reg_date = pd.to_datetime(row['created_at']).strftime('%Y-%m-%d')
                st.caption(f"注册日期: {reg_date}")
            
            with col2:
                bal = row['balance'] if row['balance'] is not None else 0
                disc = row['current_discount'] if row['current_discount'] is not None else 1.0
                st.metric("当前余额", f"¥{bal}")
                st.metric("权益等级", f"{int(disc*100)}折" if disc < 1 else "无折扣")

            st.divider()
            
            # 修改备注表单
            st.subheader("📝 修改档案")
            with st.form("edit_note"):
                new_note = st.text_area("备注内容", value=row['note'] if row['note'] else "", height=100)
                if st.form_submit_button("💾 保存修改"):
                    run_transaction("UPDATE members SET note = :note WHERE id = :mid AND owner_username = :owner",
                                    {"note": new_note, "mid": m_id, "owner": CURRENT_USER})
                    st.success("已更新！")
                    time.sleep(1)
                    st.rerun()
            
            # 加个小按钮方便退回列表
            if st.button("🔙 返回列表"):
                 # Streamlit的trick: 虽然不能直接清空输入框，但刷新可以重置状态
                 # 或者这里什么都不做，用户自己删掉搜索词也行
                 st.rerun()

        # --- 情况 B: 多人 (或全部) -> 显示表格清单 ---
        else:
            st.write(f"共找到 **{len(df)}** 位会员")
            
            # 整理一下表格显示的列名，让它好看点
            display_df = df[['name', 'phone', 'balance', 'note', 'created_at']].copy()
            display_df.columns = ['姓名', '手机号', '余额', '备注', '注册时间']
            
            # 简单的格式化
            display_df['余额'] = display_df['余额'].fillna(0).apply(lambda x: f"¥{x}")
            display_df['注册时间'] = pd.to_datetime(display_df['注册时间']).dt.strftime('%Y-%m-%d')
            
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            st.caption("💡 提示：输入精准的 **姓名** 或 **手机号** 即可进入编辑模式。")
# ==========================
# 功能 D: 账目查询 (优化日期显示)
# ==========================
elif menu == "账目查询":
    st.header("📊 账目流水")
    sql = """
        SELECT t.date, m.name, t.type, t.amount, t.detail, t.signature
        FROM transactions t
        JOIN members m ON t.member_id = m.id
        WHERE t.owner_username = :owner
        ORDER BY t.id DESC LIMIT 20
    """
    df = run_query(sql, {"owner": CURRENT_USER})
    
    if not df.empty:
        for i, row in df.iterrows():
            # 【关键修改】格式化日期
            # 先转成 datetime 对象，再格式化为 "年-月-日 时:分:秒"
            try:
                fmt_date = pd.to_datetime(row['date']).strftime('%Y-%m-%d %H:%M:%S')
            except:
                fmt_date = row['date'] # 如果转换失败就显示原样
            
            # 标题显示：时间 - 姓名 - 金额
            with st.expander(f"{fmt_date} | {row['name']} | ¥{row['amount']}"):
                st.write(f"**类型:** {row['type']}")
                st.write(f"**详情:** {row['detail']}")
                if row['signature']:
                    st.write("**签字:**")
                    st.image(base64.b64decode(row['signature']), width=200)
    else:
        st.info("暂无数据")