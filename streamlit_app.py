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

menu = st.sidebar.radio("功能菜单", ["消费结账", "会员充值", "会员管理", "账目查询"])
st.title(f"💅 {menu}")

# ==========================
# 功能: 会员充值/新建 (合并版)
# ==========================
if menu == "会员充值":
    st.header("💰 会员充值 ")
    
    # 1. 统一搜索入口
    search_term = st.text_input("🔍 输入手机号/姓名/尾号 (回车确认)", placeholder="老客直接搜，新客输入手机号自动新建").strip()
    
    if search_term:
        # --- 搜索逻辑 ---
        sql = """
            SELECT m.id, m.name, m.phone, a.balance, a.current_discount 
            FROM members m 
            JOIN accounts a ON m.id = a.member_id 
            WHERE (m.phone = :term OR m.name ILIKE :term OR m.phone LIKE :tail)
            AND m.owner_username = :owner
        """
        tail_param = f"%{search_term}" if (len(search_term) == 4 and search_term.isdigit()) else "impossible_match"
        df = run_query(sql, {"term": search_term, "tail": tail_param, "owner": CURRENT_USER})
        
        # === 分支 A: 找到了 -> 显示充值界面 ===
        if not df.empty:
            # 默认取第一个匹配项
            row = df.iloc[0]
            m_id, m_name, m_bal, m_disc = int(row['id']), row['name'], float(row['balance']), float(row['current_discount'])
            m_phone = row['phone']

            st.success(f"✅ 找到会员: **{m_name}** ({m_phone})")
            st.info(f"当前余额: **¥{m_bal}** | 当前折扣: **{int(m_disc*100) if m_disc<1 else '无'}**")
            
            st.divider()
            st.subheader("💸 会员充值")
            
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

                if st.form_submit_button("确认充值"):
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

        # === 分支 B: 没找到 -> 显示新建界面 (自动带入开卡充值) ===
        else:
            st.warning(f"⚠️ 未找到 '{search_term}'，请录入新会员")
            
            with st.form("new_member_form"):
                col1, col2 = st.columns(2)
                # 如果搜索的是手机号，自动填入
                default_phone = search_term if search_term.isdigit() and len(search_term) >= 7 else ""
                name = col1.text_input("姓名")
                phone = col2.text_input("手机号", value=default_phone)
                birthday = st.date_input("生日", value=datetime(2000, 1, 1), min_value=datetime(1950, 1, 1))
                note = st.text_area("备注")
                
                st.divider()
                st.write("**💰 开卡设置 (选填)**")
                initial_amount = st.number_input("开卡充值金额 (¥)", min_value=0.0, step=100.0)
                initial_discount = st.selectbox("开卡折扣", [1.0, 0.95, 0.9, 0.88, 0.8, 0.7, 0.6], 
                                              format_func=lambda x: "原价" if x==1.0 else f"{int(x*100) if x*100%10!=0 else int(x*10)}折")

                submitted = st.form_submit_button("➕ 创建并开卡")
                
                if submitted:
                    if not name or not phone:
                        st.error("姓名和手机号必填！")
                    else:
                        try:
                            # 1. 插入会员
                            sql_member = """
                                INSERT INTO members (name, phone, birthday, note, owner_username) 
                                VALUES (:name, :phone, :birthday, :note, :owner)
                            """
                            run_transaction(sql_member, {
                                "name": name, "phone": phone, 
                                "birthday": birthday, "note": note, "owner": CURRENT_USER
                            })

                            # 2. 获取新ID
                            df_new = run_query("SELECT id FROM members WHERE phone = :phone AND owner_username = :owner", 
                                           {"phone": phone, "owner": CURRENT_USER})
                            m_id = int(df_new.iloc[0]['id'])

                            # 3. 插入账户 (带初始余额)
                            run_transaction("INSERT INTO accounts (member_id, balance, current_discount) VALUES (:mid, :bal, :disc)", 
                                            {"mid": m_id, "bal": initial_amount, "disc": initial_discount})
                            
                            # 4. 如果有充值，记录流水
                            if initial_amount > 0:
                                run_transaction(
                                    """INSERT INTO transactions (member_id, type, amount, detail, date, owner_username) 
                                    VALUES (:mid, 'RECHARGE', :amt, :detail, NOW(), :owner)""",
                                    {"mid": m_id, "amt": initial_amount, "detail": f"开卡充值{initial_amount}, 初始折扣{initial_discount}", "owner": CURRENT_USER}
                                )

                            st.success(f"🎉 会员 {name} 创建成功！(余额: ¥{initial_amount})")
                            time.sleep(1)
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"创建失败 (可能是手机号重复): {e}")       

            
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
            WHERE (m.phone = :term OR m.name ILIKE :term OR m.phone LIKE :tail)
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
# 功能: 会员管理 (全能编辑版)
# ==========================
elif menu == "会员管理":
    st.header("🔍 会员档案管理")
    
    # 1. 搜索框
    search_term = st.text_input("搜索会员 (支持姓名/全号/尾号)", placeholder="留空则显示全部会员").strip()
    
    # 2. 构造 SQL
    sql = """
        SELECT m.id, m.name, m.phone, m.birthday, m.note, m.created_at, 
               a.balance, a.current_discount 
        FROM members m 
        LEFT JOIN accounts a ON m.id = a.member_id 
        WHERE m.owner_username = :owner
    """
    params = {"owner": CURRENT_USER}
    
    if search_term:
        sql += " AND (m.phone = :term OR m.name ILIKE :term OR m.phone LIKE :tail)"
        params["term"] = search_term
        params["tail"] = f"%{search_term}" if (len(search_term)==4 and search_term.isdigit()) else "impossible_match"
    
    sql += " ORDER BY m.id DESC"
    df = run_query(sql, params)
    
    # 3. 界面逻辑
    if df.empty:
        st.info("暂无数据")
    else:
        # --- 情况 A: 刚好锁定 1 个人 -> 进入【全能编辑模式】 ---
        if len(df) == 1:
            row = df.iloc[0]
            m_id = int(row['id'])
            
            st.success(f"正在编辑: **{row['name']}**")
            
            with st.form("edit_full_profile"):
                st.caption("👇 您可以在下方直接修改任何信息")
                
                # 第一行：基本资料
                c1, c2 = st.columns(2)
                new_name = c1.text_input("姓名", value=row['name'])
                new_phone = c2.text_input("手机号", value=row['phone'])
                
                # 第二行：生日与余额
                c3, c4 = st.columns(2)
                # 处理生日格式，防止空值报错
                try:
                    default_birth = pd.to_datetime(row['birthday']).date()
                except:
                    default_birth = datetime(2000, 1, 1)
                new_birth = c3.date_input("生日", value=default_birth, min_value=datetime(1900, 1, 1))
                
                # 余额修改 (特别标注)
                current_bal = float(row['balance']) if row['balance'] is not None else 0.0
                new_balance = c4.number_input("账户余额 (¥)", value=current_bal, step=10.0, help="可以直接修改余额进行平账")
                
                # 第三行：备注
                new_note = st.text_area("备注", value=row['note'] if row['note'] else "", height=100)
                
                # 保存按钮
                if st.form_submit_button("💾 保存所有修改", type="primary"):
                    try:
                        # 1. 更新 members 表 (基本信息)
                        # 注意：这里加上 owner 限制，防止误改
                        sql_member = """
                            UPDATE members 
                            SET name = :name, phone = :phone, birthday = :birth, note = :note 
                            WHERE id = :mid AND owner_username = :owner
                        """
                        run_transaction(sql_member, {
                            "name": new_name, "phone": new_phone, 
                            "birth": new_birth, "note": new_note, 
                            "mid": m_id, "owner": CURRENT_USER
                        })
                        
                        # 2. 更新 accounts 表 (余额)
                        sql_account = "UPDATE accounts SET balance = :bal WHERE member_id = :mid"
                        run_transaction(sql_account, {"bal": new_balance, "mid": m_id})
                        
                        st.success("✅ 档案已更新！")
                        time.sleep(1)
                        st.rerun()
                        
                    except Exception as e:
                        # 捕捉手机号重复的错误
                        if "UniqueViolation" in str(e) or "unique constraint" in str(e):
                            st.error(f"保存失败：手机号 {new_phone} 已存在，请检查！")
                        else:
                            st.error(f"保存失败: {e}")

            # 返回按钮
            if st.button("🔙 返回列表"):
                 st.rerun()

        # --- 情况 B: 多人 -> 显示表格 ---
        else:
            st.write(f"共找到 **{len(df)}** 位会员")
            display_df = df[['name', 'phone', 'balance', 'note', 'created_at']].copy()
            display_df.columns = ['姓名', '手机号', '余额', '备注', '注册时间']
            display_df['余额'] = display_df['余额'].fillna(0).apply(lambda x: f"¥{x}")
            display_df['注册时间'] = pd.to_datetime(display_df['注册时间']).dt.strftime('%Y-%m-%d')
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            st.caption("💡 提示：输入 **姓名** 或 **手机号** 锁定一人后，即可修改全部资料。")
# ==========================
# 功能 D: 账目查询 (Altair 分组柱状图版 - 再次修正)
# ==========================
elif menu == "账目查询":
    st.header("📊 经营数据分析")
    
    import altair as alt # 确保 altair 已导入
    
    # --- 1. 顶部图表：近7天经营趋势 (分组柱状图) ---
    st.subheader("📈 近7天经营趋势")
    
    chart_sql = """
        SELECT date(date) as day, type, SUM(amount) as total
        FROM transactions
        WHERE owner_username = :owner 
        AND date >= CURRENT_DATE - INTERVAL '6 days'
        GROUP BY day, type
        ORDER BY day
    """
    chart_df = run_query(chart_sql, {"owner": CURRENT_USER})
    
    if not chart_df.empty:
        chart_df['type_cn'] = chart_df['type'].map({'RECHARGE': '充值收入', 'SPEND': '消费扣款'})
        
        # 确保所有日期都有充值和消费两行，没有数据的填0，防止柱子缺失
        # 获取所有日期
        all_days = pd.date_range(end=datetime.now().date(), periods=7, freq='D')
        all_types = ['充值收入', '消费扣款']
        
        # 创建一个完整的日期-类型组合
        full_index = pd.MultiIndex.from_product([all_days, all_types], names=['day', 'type_cn'])
        
        # 将原始数据重新索引，缺失值填0
        chart_df_pivot = chart_df.set_index(['day', 'type_cn'])['total'].reindex(full_index, fill_value=0).reset_index()
        
        # 确保日期格式正确， Altair 需要日期类型
        chart_df_pivot['day'] = pd.to_datetime(chart_df_pivot['day'])

        chart = alt.Chart(chart_df_pivot).mark_bar().encode(
            # X轴：类型 (充值/消费)，在每个日期组内左右排开
            x=alt.X('type_cn:N', axis=alt.Axis(title=None, labels=True)),
            
            # Y轴：金额
            y=alt.Y('total:Q', axis=alt.Axis(title='金额 (¥)')),
            
            # 颜色：根据类型变色
            color=alt.Color('type_cn:N', 
                            scale=alt.Scale(domain=['消费扣款', '充值收入'], range=['#FF4B4B', '#00C805']),
                            legend=alt.Legend(title="类型")),
            
            # 【关键】列：按日期分组 (每个日期显示一组柱子)
            column=alt.Column('day:T', 
                              header=alt.Header(titleOrient="bottom", labelOrient="bottom", format='%m-%d'),
                              title='日期'), # 在每个小图的标题显示日期
            
            # 鼠标悬停提示
            tooltip=[
                alt.Tooltip('day:T', title='日期', format='%Y-%m-%d'),
                alt.Tooltip('type_cn:N', title='类型'),
                alt.Tooltip('total:Q', title='金额')
            ]
        ).properties(
            height=300 # 图表高度
        ).configure_header(
            titleFontSize=14,
            labelFontSize=12
        ).configure_axis(
            labelFontSize=10, # 调整 x 轴标签字体大小
            titleFontSize=12  # 调整 y 轴标签字体大小
        )
        
        st.altair_chart(chart, use_container_width=True)
        
    else:
        st.caption("最近7天暂无数据")
        
    st.divider()

    # --- 2. 查询过滤器 ---
    st.subheader("🔍 详细账目查询")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        # 搜索框
        search_term = st.text_input("👤 搜索会员 (姓名/全号/尾号)").strip()
    with col2:
        # 日期范围 (默认本月)
        today = datetime.now()
        first_day = today.replace(day=1)
        date_range = st.date_input("📅 选择日期范围", value=(first_day, today))

    # --- 3. 构造查询 SQL ---
    sql = """
        SELECT t.date, m.name, m.phone, t.type, t.amount, t.detail, t.signature
        FROM transactions t
        JOIN members m ON t.member_id = m.id
        WHERE t.owner_username = :owner
    """
    params = {"owner": CURRENT_USER}

    # 加入搜索条件
    if search_term:
        sql += " AND (m.phone = :term OR m.name ILIKE :term OR m.phone LIKE :tail)"
        params["term"] = search_term
        params["tail"] = f"%{search_term}" if (len(search_term)==4 and search_term.isdigit()) else "impossible_match"

    # 加入日期条件
    # st.date_input 返回的是一个元组，可能只有开始日期，也可能都有
    if isinstance(date_range, tuple):
        if len(date_range) > 0:
            sql += " AND t.date >= :start_date"
            params["start_date"] = date_range[0]
        if len(date_range) > 1:
            # 结束日期要加一天，因为数据库存的是 '2023-11-20 12:00'，而查询 '2023-11-20' 默认是0点
            # 所以要查到 '2023-11-21 00:00' 之前
            import datetime as dt
            end_date = date_range[1] + dt.timedelta(days=1)
            sql += " AND t.date < :end_date"
            params["end_date"] = end_date

    sql += " ORDER BY t.id DESC"
    
    # 执行查询
    df = run_query(sql, params)
    
    # --- 4. 统计卡片 (基于筛选结果) ---
    if not df.empty:
        # 计算合计
        total_recharge = df[df['type'] == 'RECHARGE']['amount'].sum()
        total_spend = df[df['type'] == 'SPEND']['amount'].sum()
        
        # 展示统计
        m1, m2, m3 = st.columns(3)
        m1.metric("笔数", f"{len(df)} 笔")
        m2.metric("充值合计 (收入)", f"¥{total_recharge:,.2f}")
        m3.metric("消费合计 (消耗)", f"¥{total_spend:,.2f}")
        
        # --- 5. 详细列表 ---
        st.write("---")
        for i, row in df.iterrows():
            # 日期格式化
            try:
                fmt_date = pd.to_datetime(row['date']).strftime('%Y-%m-%d %H:%M:%S')
            except:
                fmt_date = row['date']
            
            # 定义图标
            icon = "💰" if row['type'] == 'RECHARGE' else "💅"
            color = "green" if row['type'] == 'RECHARGE' else "red"
            
            with st.expander(f"{icon} {fmt_date} | {row['name']} | :{'green' if row['type']=='RECHARGE' else 'red'}[¥{row['amount']}]"):
                st.write(f"**手机:** {row['phone']}")
                st.write(f"**详情:** {row['detail']}")
                if row['signature']:
                    st.write("**签字确认:**")
                    st.image(base64.b64decode(row['signature']), width=200)
    else:
        st.info("在此筛选条件下暂无数据")