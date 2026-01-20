import streamlit as st
from fpdf import FPDF
import pandas as pd
import os

# 程序版本号定义
VERSION = "v1.5"

# --- 1. 页面配置 ---
st.set_page_config(page_title=f"烘焙面团计算程序 {VERSION}", layout="wide")
st.markdown("""
    <style>
    [data-testid='stSidebar'] {min-width: 450px; max-width: 450px;}
    .block-container {padding-top: 1rem;} 
    </style>
    """, unsafe_allow_html=True)

# --- 2. 核心计算引擎 ---
def calculate_recipe(args):
    # 提取基础参数
    H = args['hydration_rate']
    E = args['egg_count'] * 50.0  # 默认鸡蛋重
    E_water = E * 0.75
    others_p = args['salt_p'] + args['sugar_p'] + args['butter_p'] + args['oil_p'] + args['yeast_p']
    
    # 根据模式计算总粉量 F
    if args['calc_mode'] == "锁定面粉量 (正推)":
        # 用户输入的是总粉量
        F = args['target_value']
    else:
        # 用户输入的是目标总重 T，倒推 F
        T = args['target_value']
        if args['use_milk']:
            # 牛奶含水率按 90% 折算
            F = (T - E + (E_water / 0.9)) / (1 + (H / 0.9) + others_p)
        else:
            F = (T - E + E_water) / (1 + H + others_p)

    # 计算液体添加量 (基于总粉 F)
    if args['use_milk']:
        added_liquid = (F * H - E_water) / 0.9
    else:
        added_liquid = F * H - E_water

    res = {
        "total_flour": F,
        "added_liquid": max(0, added_liquid),
        "salt": F * args['salt_p'],
        "sugar": F * args['sugar_p'],
        "butter": F * args['butter_p'],
        "oil": F * args['oil_p'],
        "yeast": F * args['yeast_p'],
        "egg": E,
        "liquid_type": "牛奶" if args['use_milk'] else "水"
    }

    # 前种/天然酵母计算逻辑
    if args['use_pre']:
        res['pre_flour'] = F * args['pre_ratio']
        res['pre_ratio_val'] = args['pre_ratio'] * 100
        res['pre_water'] = res['pre_flour'] * args['pre_hydra']
        res['pre_total'] = res['pre_flour'] + res['pre_water']
        res['pre_hydra_val'] = args['pre_hydra'] * 100
        res['main_flour'] = F - res['pre_flour']
        
        # 扣除前种里的液体
        liquid_in_pre = res['pre_water'] / (0.9 if args['use_milk'] else 1.0)
        res['main_added_liquid'] = max(0, res['added_liquid'] - liquid_in_pre)
        
        # 增强天然酵母逻辑
        if "Poolish" in args['pre_template']:
            res['pre_yeast_p'] = 0.001 if args['pre_hydra'] >= 1.0 else 0.002
            res['pre_class'] = "Poolish"
            res['pre_yeast_val'] = res['pre_flour'] * res['pre_yeast_p']
        elif "Biga" in args['pre_template']:
            res['pre_yeast_p'] = 0.003 if args['pre_hydra'] >= 0.5 else 0.005
            res['pre_class'] = "Biga"
            res['pre_yeast_val'] = res['pre_flour'] * res['pre_yeast_p']
        else:
            # 天然酵母/鲁邦种：不需要额外添加干酵母制作前种
            res['pre_yeast_p'] = 0.0
            res['pre_class'] = "Sourdough"
            res['pre_yeast_val'] = 0.0 
    else:
        res['pre_flour'] = res['pre_water'] = res['pre_total'] = 0
        res['pre_yeast_val'] = 0
        res['main_flour'] = F
        res['main_added_liquid'] = res['added_liquid']
        res['pre_class'] = "无"

    total_water_content = (res['added_liquid'] * (0.9 if args['use_milk'] else 1.0)) + E_water
    # 实际总重
    res['actual_total'] = F + res['added_liquid'] + E + res['salt'] + res['sugar'] + res['butter'] + res['oil'] + res['yeast'] + (res.get('pre_yeast_val', 0) if args['use_pre'] else 0)
    
    res['actual_hydration'] = (total_water_content / F) * 100 if F > 0 else 0
    res['total_liquid_req'] = total_water_content
    
    return res

# --- 3. 前种比例建议 ---
def get_preferment_ratio_advice(bread_type, pre_class):
    table = {
        "法棍": {"Poolish": ("40%", "50%"), "Sourdough": ("15%", "20%")},
        "欧包": {"Poolish": ("20%", "30%"), "Sourdough": ("15%", "20%")},
        "吐司": {"Poolish": ("30%", "40%"), "Sourdough": ("15%", "25%")},
        "披萨": {
            "Biga": ("30%", "50%"), 
            "Sourdough": ("10%", "20%"),
            "Poolish": ("20%", "30%") 
        },
        "包子": {"Poolish": ("20%", "30%"), "Sourdough": ("20%", "40%")}
    }
    
    # 不匹配警告
    if bread_type in table and pre_class not in table[bread_type]:
        return f"⚠️ **提示：制作{bread_type}通常不建议使用{pre_class}，建议核对配方。**"

    # 正常建议
    if bread_type in table and pre_class in table[bread_type]:
        safe, tasty = table[bread_type][pre_class]
        
        # 针对披萨+Poolish做特殊备注
        extra_note = ""
        if bread_type == "披萨" and pre_class == "Poolish":
            extra_note = " (高手可尝试40%-50%)"
            
        return f"🔧 **前种比例建议：安全比例 {safe}，好口感比例 {tasty}{extra_note}。**"
        
    return None

# --- 4. 面粉适配建议 ---
def get_advanced_advice(f, b):
    matrix = {
        "全麦粉": {
            "吐司": "全麦粉筋度稍弱且麸皮切割面筋。建议补水率增加5-8%，并加入20%高筋粉混合。",
            "欧包": "天然适配。建议采用浸泡法，补水增加5%。",
            "法棍": "麦香好但孔洞小。建议水合率增加10%。"
        },
        "全黑麦粉": {
            "欧包": "黑麦几乎无筋度，粘性大。强烈建议混合70%以上高筋粉，或使用酸种发酵以改善组织。",
            "吐司": "不建议制作纯黑麦吐司，体积会很小。建议添加量不超过30%。",
            "法棍": "不推荐纯黑麦。可作为风味添加，比例10-15%。"
        },
        "T65": {
            "法棍": "经典组合。吸水率68-72%。建议使用冰水控温。",
            "吐司": "Q弹，建议配合20%前种，水合率68%。"
        },
        "中筋粉": {
            "包子": "最佳选择。水合率50-52%。",
            "吐司": "筋度不足，需降低水合率5%。"
        },
        "吐司粉": {
            "吐司": "吸水性强（70-75%）。建议后加水法。",
            "披萨": "筋度高易回缩，水合率65%。"
        }
    }
    return matrix.get(f, {}).get(b, f"当前使用{f}制作{b}，建议根据吸水率微调。")

# --- 披萨专用前种建议 ---
def get_pizza_pre_advice(b_type):
    if b_type == "披萨":
        return "🍕 **披萨前种建议：** Poolish 延展性极佳，底更酥脆，适合美式/盘披萨；Biga 筋度强、支撑力好，口感耐嚼，是意式拿波里披萨的经典选择。"
    return ""

# --- 5. 侧边栏输入 ---
st.sidebar.header("🍞 参数设置")

calc_mode = st.sidebar.radio("计算模式", ["锁定总重 (倒推)", "锁定面粉量 (正推)"], horizontal=True)
c1, c2 = st.sidebar.columns(2)

with c1:
    b_type = st.selectbox("产品类型", ["吐司", "欧包", "披萨", "包子", "法棍"])
    f_type = st.selectbox("面粉类型", ["高筋粉", "中筋粉", "全麦粉", "全黑麦粉", "T65", "吐司粉"])
    
    if calc_mode == "锁定总重 (倒推)":
        target_val = st.number_input("目标面团总重 (g)", value=500.0, step=10.0)
    else:
        target_val = st.number_input("目标总粉量 (g)", value=250.0, step=10.0, help="指主面团面粉+前种面粉的总和")
        
    hydra_p = st.number_input("目标水合率 (%)", value=70.0, step=0.1, format="%.1f") / 100
    eggs = st.number_input("鸡蛋个数", min_value=0, value=0, step=1, help="注：本程序采用严格水合率算法，默认鸡蛋液含水75%计入总水量。")

with c2:
    s_p = st.number_input("盐 (%)", value=2.0, step=0.1, format="%.1f") / 100
    su_p = st.number_input("糖/蜂蜜 (%)", value=0.0, step=0.1, format="%.1f") / 100
    bu_p = st.number_input("黄油 (%)", value=0.0, step=0.1, format="%.1f") / 100
    oi_p = st.number_input("橄榄油 (%)", value=0.0, step=0.1, format="%.1f") / 100
    ye_p = st.number_input("主面团酵母 (%)", value=1.0, step=0.1, format="%.1f") / 100
    use_milk = st.checkbox("使用牛奶代替水")

st.sidebar.divider()
use_pre = st.sidebar.toggle("是否使用前种")
pre_template = "Poolish"
pre_r, pre_h = 0.0, 0.0
if use_pre:
    pre_template = st.sidebar.selectbox("选择前种类型", ["Poolish (液种)", "Biga (意式硬种)", "天然酵母 (鲁邦种)"])
    pc1, pc2 = st.sidebar.columns(2)
    pre_r = pc1.number_input("前种占比(%)", value=20.0, step=0.1, format="%.1f") / 100
    default_h = 100.0 if "天然酵母" in pre_template else (100.0 if "Poolish" in pre_template else 50.0)
    pre_h = pc2.number_input("前种水合率(%)", value=default_h, step=0.1, format="%.1f") / 100

st.sidebar.divider()
# --- 修改：温度控制开关化 ---
use_ddt = st.sidebar.toggle("启用温度控制 (DDT)")
temp_target, temp_room, temp_flour, temp_friction, temp_pre = 26.0, 24.0, 24.0, 5.0, 0.0

if use_ddt:
    st.sidebar.caption("输入环境参数，自动计算建议水温")
    # 使用列布局
    t1, t2 = st.sidebar.columns(2)
    with t1:
        temp_target = st.number_input("目标DDT (℃)", value=26.0, step=0.5)
        temp_flour = st.number_input("粉温 (℃)", value=24.0, step=0.5)
    with t2:
        temp_room = st.number_input("室温 (℃)", value=24.0, step=0.5)
        temp_friction = st.number_input("摩擦升温 (℃)", value=5.0, step=0.5)
    if use_pre:
        temp_pre = st.sidebar.number_input("前种温度 (℃)", value=temp_room, step=0.5)

# 传入 use_ddt 状态供后续使用
data = calculate_recipe({
    "calc_mode": calc_mode, "target_value": target_val, 
    "hydration_rate": hydra_p, "egg_count": eggs,
    "salt_p": s_p, "sugar_p": su_p, "butter_p": bu_p, "oil_p": oi_p, "yeast_p": ye_p,
    "use_milk": use_milk, "use_pre": use_pre, "pre_ratio": pre_r, "pre_hydra": pre_h, "pre_template": pre_template
})
data['use_ddt'] = use_ddt # 记录状态

# DDT 计算
water_msg = ""
if use_ddt:
    ddt_factors = 4 if use_pre else 3
    temp_total_req = temp_target * ddt_factors
    temp_current_sum = temp_room + temp_flour + temp_friction + (temp_pre if use_pre else 0)
    temp_water_rec = temp_total_req - temp_current_sum
    water_msg = f"{temp_water_rec:.1f} ℃"
    if temp_water_rec < 5: water_msg += " (需加冰)"

current_advice = get_advanced_advice(f_type, b_type)
pizza_advice = get_pizza_pre_advice(b_type) if use_pre else ""

# --- 6. 右侧显示 ---
st.title(f"🔍 烘焙面团计算程序 ({VERSION})")

if pizza_advice:
    st.info(pizza_advice)

st.success(f"🌾 **专业适配建议：** {current_advice}")

k1, k2, k3, k4 = st.columns(4)
k1.metric("最终面团总重", f"{data['actual_total']:.1f} g")
k2.metric("实际总水合率", f"{data['actual_hydration']:.1f} %")
k3.metric("总面粉量", f"{data['total_flour']:.1f} g")
# 只有开启开关才显示建议水温
if use_ddt:
    k4.metric("建议液体温度", water_msg)
else:
    k4.empty()

st.divider()
col_left, col_right = st.columns(2)

total_flour_base = data['total_flour']
def calc_pct(val):
    if total_flour_base == 0: return "0%"
    return f"{(val / total_flour_base * 100):.1f}%"

with col_left:
    st.subheader("一、主面团清单")
    df_main = pd.DataFrame({
        "配料项目": ["主面粉", f"投料{data['liquid_type']}", "鸡蛋", "盐", "糖/蜂蜜", "黄油", "橄榄油", "酵母"],
        "重量 (g)": [
            f"{data['main_flour']:.1f}", f"{data['main_added_liquid']:.1f}", f"{data['egg']:.1f}", 
            f"{data['salt']:.1f}", f"{data['sugar']:.1f}", f"{data['butter']:.1f}", 
            f"{data['oil']:.1f}", f"{data['yeast']:.2f}"
        ],
        "烘焙百分比": [
            calc_pct(data['main_flour']), calc_pct(data['main_added_liquid']), calc_pct(data['egg']),
            calc_pct(data['salt']), calc_pct(data['sugar']), calc_pct(data['butter']),
            calc_pct(data['oil']), calc_pct(data['yeast'])
        ]
    })
    st.table(df_main)

pre_advice_text = ""
with col_right:
    is_warning = False
    if use_pre:
        st.subheader("二、前种配置详情")
        df_pre = pd.DataFrame({
            "配置项": ["前种类型", "前种占比", "前种总重", "前种面粉", "前种水量", "前种水合率"],
            "数值": [data['pre_class'], f"{data['pre_ratio_val']:.1f}%", f"{data['pre_total']:.1f}g", f"{data['pre_flour']:.1f}g", f"{data['pre_water']:.1f}g", f"{data['pre_hydra_val']:.1f}%"]
        })
        st.table(df_pre)

        ratio_advice = get_preferment_ratio_advice(b_type, data['pre_class'])
        if ratio_advice:
            if "不建议" in ratio_advice:
                st.error(ratio_advice)
                is_warning = True
            else:
                st.warning(ratio_advice)

    if not is_warning:
        if data['pre_class'] == "Poolish":
            pre_advice_text = f"""
a. 配料方案： 面粉 {data['pre_flour']:.1f}g + 水 {data['pre_water']:.1f}g + 酵母 {data['pre_yeast_val']:.2f}g
b. 制作核心： 搅拌至糊状无干粉即可。
c. 发酵建议： 20℃-22℃ 条件下 12-15 小时。
            """
            st.info("🧪 **Poolish (液种) 操作建议：**")
            st.markdown(pre_advice_text)
        elif data['pre_class'] == "Biga":
            pre_advice_text = f"""
a. 配料方案： 面粉 {data['pre_flour']:.1f}g + 水 {data['pre_water']:.1f}g + 酵母 {data['pre_yeast_val']:.2f}g
b. 制作核心： 不要揉光滑，只需抓拌成棉絮状。
c. 发酵建议： 16℃-18℃ 条件下16-24 小时。
            """
            st.info("🧪 **Biga (意式硬种) 操作建议：**")
            st.markdown(pre_advice_text)
        elif data['pre_class'] == "Sourdough":
            pre_advice_text = f"""
a. 鲁邦种准备： 确保鲁邦种（水合率 {data['pre_hydra_val']:.0f}%）已处于活跃状态，体积膨胀至少2倍。
b. 计算说明： 已自动从总粉量和总水量中扣除鲁邦种自带的粉和水。
c. 发酵建议： 天然酵母发酵较慢，建议延长一发时间。
            """
            st.info("🧪 **Sourdough (天然酵母) 操作建议：**")
            st.markdown(pre_advice_text)

# --- 7. PDF 导出 (字体兼容修复版) ---
def clean_emoji(text):
    if not text: return ""
    text = text.replace("🔧", ">>").replace("⚠️", "[!]").replace("🍕", "[披萨]").replace("🧪", "*")
    return text

class RecipePDF(FPDF):
    def header(self):
        # --- 修改1：使用绝对路径，锁定字体文件为 font.ttf ---
        current_dir = os.path.dirname(os.path.abspath(__file__))
        font_path = os.path.join(current_dir, "font.ttf")
        
        self.font_ok = False
        if os.path.exists(font_path):
            # --- 修改2：注册名为 "Font" ---
            self.add_font("Font", "", font_path)
            self.add_font("Font", "B", font_path)
            self.set_font("Font", size=18)
            self.font_ok = True
            self.cell(0, 15, "烘焙配方报告", align='C', new_x="LMARGIN", new_y="NEXT")
        else:
            self.set_font("Helvetica", 'B', size=18)
            self.cell(0, 15, "Baking Recipe Report", align='C', new_x="LMARGIN", new_y="NEXT")
            self.set_font("Helvetica", size=10)
            self.set_text_color(255, 0, 0)
            self.cell(0, 5, "Warning: font.ttf not found in script directory.", align='C', new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
        
        if self.font_ok:
            self.set_font("Font", size=10)
            self.cell(0, 5, f"产品类型: {b_type}  |  面粉类型: {f_type}", align='C', new_x="LMARGIN", new_y="NEXT")
        else:
            # --- 修改3：无字体时，使用英文占位，防止崩溃 ---
            self.set_font("Helvetica", size=10)
            self.cell(0, 5, "Type: (See Web UI) | Flour: (See Web UI)", align='C', new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-10)
        if hasattr(self, 'font_ok') and self.font_ok:
            self.set_font("Font", size=8) # 修改4：使用 Font
        else:
            self.set_font("Helvetica", size=8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 8, f"Version: {VERSION}", align='R')

    def draw_compact_table(self, title, df):
        if hasattr(self, 'font_ok') and self.font_ok:
            self.set_font("Font", size=9) # 修改5：使用 Font
        else:
            self.set_font("Helvetica", size=9)
        self.set_text_color(0, 0, 0)
        self.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        with self.table(width=170, padding=1.0, line_height=4.5, first_row_as_headings=False) as table:
            row = table.row()
            for col in df.columns:
                row.cell(str(col))
            if hasattr(self, 'font_ok') and self.font_ok:
                for _, r in df.iterrows():
                    row = table.row()
                    for val in r:
                        row.cell(str(val) if not isinstance(val, float) else f"{val:.1f}")
            else:
                row = table.row()
                row.cell("Font Missing")
        self.ln(1)

def export_pdf():
    pdf = RecipePDF()
    pdf.set_margins(20, 10, 10)
    pdf.add_page()
    
    # 1. Summary
    df_final = pd.DataFrame({
        "项目": ["总粉量", "总液体量", "最终面团", "总水合率"],
        "数值": [
            f"{data['total_flour']:.1f}g",
            f"{data['total_liquid_req']:.1f}g",
            f"{data['actual_total']:.1f}g",
            f"{data['actual_hydration']:.1f}%"
        ]
    })
    pdf.draw_compact_table("1. 数据汇总 (Summary)", df_final)

    # 2. Main Dough
    pdf.draw_compact_table("2. 主面团投料 (Main Dough)", df_main)

    # 3. Preferment
    if use_pre:
        df_pre_pdf = pd.DataFrame({
            "项目": ["前种类型", "前种占比", "前种总重", "前种粉", "前种水量", "前种水合率"],
            "值": [
                data['pre_class'],
                f"{data['pre_ratio_val']:.1f}%",
                f"{data['pre_total']:.1f}g",
                f"{data['pre_flour']:.1f}g",
                f"{data['pre_water']:.1f}g",
                f"{data['pre_hydra_val']:.1f}%"
            ]
        })
        pdf.draw_compact_table("3. 前种详情 (Preferment)", df_pre_pdf)
        
        ratio_advice = get_preferment_ratio_advice(b_type, data['pre_class'])
        is_pdf_warning = False
        
        if pdf.font_ok and ratio_advice:
            pdf.ln(1)
            pdf.set_font("Font", size=9) # 修改6：使用 Font
            if "不建议" in ratio_advice:
                is_pdf_warning = True
                pdf.set_text_color(200, 0, 0)
            else:
                pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 4, clean_emoji(ratio_advice).replace("**", "").strip())
            pdf.set_text_color(0, 0, 0)
            
        if pdf.font_ok and pre_advice_text and not is_pdf_warning:
            pdf.ln(1)
            pdf.set_font("Font", size=8) # 修改7：使用 Font
            pdf.multi_cell(0, 4, clean_emoji(pre_advice_text).strip())
            pdf.ln(3) 

    # 4. Temperature Control (Conditional)
    section_num = 4
    if data['use_ddt']:
        df_temp = pd.DataFrame({
            "参数": ["目标DDT", "室温", "粉温", "摩擦升温", "建议液体温度"],
            "数值 (℃)": [
                f"{temp_target} ℃", f"{temp_room} ℃", f"{temp_flour} ℃", f"{temp_friction} ℃", water_msg
            ]
        })
        # 使用双列 Grid 布局 (保持 1.41 版本逻辑)
        temp_items = [
            ("目标DDT", f"{temp_target}"), ("室温", f"{temp_room}"),
            ("粉温", f"{temp_flour}"), ("摩擦升温", f"{temp_friction}")
        ]
        if use_pre: temp_items.append(("前种温度", f"{temp_pre}"))
        temp_items.append(("建议水温", water_msg))
        
        grid_data = []
        for i in range(0, len(temp_items), 2):
            row = []
            row.append(temp_items[i][0])
            row.append(temp_items[i][1])
            if i + 1 < len(temp_items):
                row.append(temp_items[i+1][0])
                row.append(temp_items[i+1][1])
            else:
                row.append("")
                row.append("")
            grid_data.append(row)
        
        df_temp_grid = pd.DataFrame(grid_data, columns=["参数", "数值", "参数", "数值"])
        pdf.draw_compact_table(f"{section_num}. 温度控制 (Temperature)", df_temp_grid)
        section_num += 1

    # 5/4. Expert Advice (Separated)
    if pdf.font_ok:
        pdf.ln(2)
        pdf.set_font("Font", size=10) # 修改8：使用 Font
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, f"{section_num}. 专家建议:", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_font("Font", size=8) # 修改9：使用 Font
        pdf.set_text_color(0, 0, 0)
        
        # 1. 面粉适配建议
        pdf.write(4, "a. 面粉适配")
        pdf.multi_cell(0, 4, current_advice)
        # 2. 披萨前种建议
        if pizza_advice:
            clean_pizza = pizza_advice.replace("**", "").replace("🍕 ", "")
            pdf.write(4, "b. ") 
            pdf.multi_cell(0, 4, clean_pizza)
            pdf.ln(0.5)

    return bytes(pdf.output())

# --- 8. PDF 导出按钮 ---
st.divider()
st.subheader("📄 导出配方")

if st.button("🚀 生成配方 PDF"):
    pdf_data = export_pdf()
    st.download_button(
        label="📥 下载 PDF 文件",
        data=pdf_data,
        file_name=f"{b_type}_recipe_v1.5.pdf",
        mime="application/pdf"
    )
    # --- 修改10：检测 font.ttf ---
    current_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(current_dir, "font.ttf")
    if not os.path.exists(font_path):
        st.warning("⚠️ 检测到缺少中文字体文件 (font.ttf)，生成的 PDF 将仅显示基础英文框架。")