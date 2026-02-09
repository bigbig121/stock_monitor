import tkinter as tk
from tkinter import simpledialog, messagebox, ttk
from PIL import Image, ImageTk
import requests
import time
import threading
import ctypes
import json
import os
from datetime import datetime

import math
import random

VERSION = "0.4.3"

# ================= 配置区域 =================
CONFIG_FILE = "stock_config.json"
DEFAULT_STOCKS = [
    {"code": "sh000681", "name": "科创价格"}, 
    {"code": "sh000832", "name": "中证转债"}, 
    {"code": "sh518880", "name": "国内金价"}, # 黄金ETF，走势即金价
]

# 全局变量
STOCKS = []
labels = []
update_thread = None
root = None
last_percentages = {} # 记录上次的涨跌幅: {code: percent}
display_mode = "bar" # 显示模式: "percent" (百分比) 或 "bar" (柱状图)
show_price = True # 是否显示价格
show_volume = True # 是否显示成交量
session_max_map = {} # 本次运行期间每只股票出现过的最大涨跌幅绝对值 {code: max_percent}
current_date_str = datetime.now().strftime("%Y-%m-%d") # 当前运行日期
MA5_VOLUMES = {} # 5日均量 {code: avg_volume}

# 刷新频率（秒）
REFRESH_RATE = 1

# 字体设置 
# 使用 Microsoft YaHei UI 在 Windows 上显示更清晰
# 稍微加大字号以配合高DPI模式
FONT_CONFIG = ("Microsoft YaHei UI", 10, "bold") 
# ===========================================

def load_config():
    """加载配置文件"""
    global STOCKS, display_mode, session_max_map, show_price, show_volume
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    STOCKS = data
                    # 兼容旧版本，display_mode 保持默认
                elif isinstance(data, dict):
                    STOCKS = data.get("stocks", DEFAULT_STOCKS)
                    display_mode = data.get("display_mode", "bar")
                    show_price = data.get("show_price", True)
                    show_volume = data.get("show_volume", True)
                    
                    # 检查日期，如果是今天则恢复 session_max_map，否则重置
                    saved_date = data.get("date", "")
                    today = datetime.now().strftime("%Y-%m-%d")
                    if saved_date == today:
                        session_max_map = data.get("session_max_map", {})
                    else:
                        session_max_map = {}
        except Exception:
            STOCKS = DEFAULT_STOCKS
            session_max_map = {}
            show_price = True
            show_volume = True
    else:
        STOCKS = DEFAULT_STOCKS
        session_max_map = {}
        show_price = True
        show_volume = True

def save_config():
    """保存配置文件"""
    try:
        data = {
            "stocks": STOCKS,
            "display_mode": display_mode,
            "show_price": show_price,
            "show_volume": show_volume,
            "session_max_map": session_max_map,
            "date": datetime.now().strftime("%Y-%m-%d")
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_ma5_volumes_thread():
    """后台线程获取5日均量"""
    global MA5_VOLUMES
    print("Fetching MA5 volumes...")
    
    # 构建代码映射 (复用 get_stock_data_tencent 的逻辑)
    # 这一步是为了确保 K线接口用的是正确的 sh/sz 代码
    mapped_codes = {}
    for item in STOCKS:
        original = item["code"]
        api_code = original
        if original.startswith("csi"):
            api_code = "sh" + original[3:]
        elif original.startswith("sh1b"):
            api_code = "sh00" + original[4:]
        elif original.startswith("cns"):
            api_code = "sh" + original[3:]
        mapped_codes[original] = api_code

    for original_code, api_code in mapped_codes.items():
        # 过滤不支持K线均量查询的特殊代码 (期货/现货/外汇等)
        if original_code.startswith(("hf_", "gds_", "nf_", "Au", "Ag", "Pt")):
            continue

        try:
            # 获取6天数据，为了排除今天（如果今天已经有数据）
            url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={api_code},day,,,6,qfq"
            resp = requests.get(url, timeout=2)
            if resp.status_code != 200:
                continue
                
            data = resp.json()
            # 腾讯接口结构: data['data'][code]['day'] 或 'qfqday'
            # 注意: 如果 api_code 不存在或返回格式异常 (如 'list' object), 这里会抛出 AttributeError
            if not isinstance(data.get('data'), dict):
                continue

            stock_data = data['data'].get(api_code, {})
            days = []
            if 'day' in stock_data:
                days = stock_data['day']
            elif 'qfqday' in stock_data:
                days = stock_data['qfqday']
            
            if not days:
                continue
                
            # 排除今天的数据，只取过去的
            today = datetime.now().strftime("%Y-%m-%d")
            history_days = [d for d in days if d[0] != today]
            
            # 取最后5天
            last_5 = history_days[-5:]
            if len(last_5) > 0:
                # index 5 是成交量
                avg_vol = sum(float(d[5]) for d in last_5) / len(last_5)
                MA5_VOLUMES[original_code] = avg_vol
                print(f"MA5 for {original_code}: {avg_vol}")
                
        except Exception as e:
            print(f"Error fetching MA5 for {original_code}: {e}")

def get_stock_data_tencent(codes):
    """
    使用腾讯/新浪接口批量获取股票/期货/外汇数据
    codes: [{"code": "sh000001", "name": "上证指数"}, ...]
    """
    results = {}
    
    # 分离出需要用新浪接口查询的代码 (nf_开头 或 Au99.99等现货)
    sina_codes = []
    tencent_codes = []
    
    for s in codes:
        code = s["code"]
        # 扩展新浪接口支持的代码：期货(nf_) 和 现货(Au/Ag开头) 和 gds_开头
        if code.startswith("nf_") or code.startswith("Au") or code.startswith("Ag") or code.startswith("Pt") or code.startswith("gds_"):
            sina_codes.append(code)
        else:
            tencent_codes.append(code)
            
    # 1. 获取新浪数据 (期货/现货)
    if sina_codes:
        try:
            # 新浪现货代码通常需要加 g_ 前缀 (如 Au99.99 -> g_Au99.99)
            # 但 nf_ 开头的期货不需要
            query_list = []
            for c in sina_codes:
                if c.startswith("nf_") or c.startswith("gds_"):
                    query_list.append(c)
                else:
                    # 现货: 假设是 Au99.99 这种，尝试加 g_ (如果用户没加)
                    if not c.startswith("g_"):
                        query_list.append(f"g_{c}") # 尝试加 g_
                    else:
                        query_list.append(c)
                        
            url = f"http://hq.sinajs.cn/list={','.join(query_list)}"
            headers = {'Referer': 'http://finance.sina.com.cn'}
            resp = requests.get(url, headers=headers, timeout=2)
            content = resp.content.decode('gbk', errors='ignore')
            # 格式:
            # var hq_str_nf_AU0="黄金连续,150000,1089.00,1105.60,..."
            # var hq_str_g_Au99_99="370.00,370.00,368.50,371.80,..." 
            # var hq_str_gds_AU9999="1094.00,0,1092.00,1094.00,1102.95,..."
            
            lines = content.strip().split(';')
            for line in lines:
                if '="' not in line: continue
                try:
                    key_part = line.split('="')[0] # var hq_str_nf_AU0
                    # 提取原始 key
                    if "_str_" in key_part:
                        api_key = key_part.split('_str_')[1] # nf_AU0 or g_Au99.99 or gds_AU9999
                        
                        # 还原回用户输入的 code
                        # 如果是 g_Au99.99，用户存的是 Au99.99
                        user_code = api_key
                        if api_key.startswith("g_") and not api_key.startswith("gds_"):
                            user_code = api_key[2:]
                        
                        data_str = line.split('="')[1].strip('"')
                        data = data_str.split(',')
                        
                        # 解析逻辑
                        current_price = 0.0
                        percent = 0.0
                        
                        if api_key.startswith("nf_"): # 期货
                             if len(data) > 8:
                                current_price = float(data[8])
                                last_close = float(data[5])
                                if last_close > 0:
                                    percent = ((current_price - last_close) / last_close) * 100
                        elif api_key.startswith("gds_"): # 贵金属现货 (gds_AU9999)
                            # 格式: Current, ?, Open, High, LastClose?, Low? ...
                            # 示例: 1094.00,0,1092.00,1094.00,1102.95,1049.01,...
                            if len(data) > 4:
                                current_price = float(data[0])
                                last_close = float(data[4])
                                if last_close > 0:
                                    percent = ((current_price - last_close) / last_close) * 100
                        else: # 其他现货 (Au99.99 / g_)
                             if len(data) > 0:
                                 current_price = float(data[0])
                                 # 尝试计算涨跌幅，假设 data[4] 是昨收 (Common pattern)
                                 if len(data) > 4:
                                     last_close = float(data[4])
                                     if last_close > 0:
                                         percent = ((current_price - last_close) / last_close) * 100
                        
                        results[user_code] = (current_price, percent)
                        # 同时保存 api_key 以防万一 (但 results key 必须匹配 STOCKS 中的 code)
                        if user_code != api_key:
                             results[api_key] = (current_price, percent)

                except Exception:
                    continue
        except Exception as e:
            pass

    # 2. 获取腾讯数据 (股票/ETF/外汇/美股)
    if tencent_codes:
        # 构建 code_map 以便在解析时还原原始代码
        code_map = {}
        for code in tencent_codes:
            api_code = code
            if code.startswith("csi"):
                api_code = "sh" + code[3:]
            elif code.startswith("sh1b"):
                api_code = "sh00" + code[4:]
            elif code.startswith("cns"):
                api_code = "sh" + code[3:]
            code_map[api_code] = code

        # 使用 api_code 进行查询
        api_query_codes = list(code_map.keys())
        # 对于不需要转换的普通代码，也要确保在 code_map 里
        # (上面的循环其实已经覆盖了，因为 default api_code = code)
        
        try:
            url = f"http://qt.gtimg.cn/q={','.join(api_query_codes)}"
            resp = requests.get(url, timeout=2)
            
            # 腾讯接口返回GBK编码，需要正确解码
            content = resp.content.decode('gbk', errors='ignore')
            
            # 解析返回数据
            lines = content.strip().split(';')
            for line in lines:
                line = line.strip()
                if '="' not in line: continue
                
                # 提取代码和数据
                # line: v_sh000681="1~..."
                # 注意：对于 hf_XAU，key 可能是 hf_XAU
                try:
                    temp = line.split('="')[0]
                    # 腾讯返回的变量名通常是 v_代码，如 v_sh000681, v_hf_XAU
                    # 如果代码里包含下划线（如 hf_XAU），split('_') 会有多个部分
                    # v_hf_XAU -> ['v', 'hf', 'XAU'] -> 取 [1:] 拼接？
                    # 或者直接取 v_ 之后的部分
                    key = temp[2:] # 去掉 "v_"
                    
                    # 还原回用户输入的 code
                    original_code = code_map.get(key, key)
                    
                    data_str = line.split('="')[1].strip('"')
                    
                    # 1. 尝试普通股票格式 (~)
                    data = data_str.split('~')
                    if len(data) > 30:
                        current_price = float(data[3])
                        percent = float(data[32])
                        volume = float(data[6]) # 成交量(手)
                        results[original_code] = (current_price, percent, volume)
                        continue
                        
                    # 2. 尝试期货/外汇格式 (,)
                    data_comma = data_str.split(',')
                    if len(data_comma) > 5:
                        current_price = float(data_comma[0])
                        # 对于 hf_ 开头的代码，data_comma[1] 是涨跌幅百分比
                        if key.startswith('hf_'):
                            percent = float(data_comma[1])
                        else:
                            # 其他逗号分隔的数据 (如果有的话)，暂时保持原有逻辑或默认为0
                            # 或者尝试计算: change_amount = data_comma[1]
                            change_amount = float(data_comma[1])
                            if current_price != 0:
                                last_close = current_price - change_amount
                                if last_close != 0:
                                    percent = (change_amount / last_close) * 100
                                else:
                                    percent = 0.0
                            else:
                                percent = 0.0
                        
                        results[original_code] = (current_price, percent, 0) # 暂不支持量
                except Exception:
                    continue
        except Exception as e:
            # print(f"Error: {e}")
            pass
            
    return results

def search_stocks_sina(keyword):
    """
    使用新浪接口搜索股票
    返回列表: [(code, name), ...]
    """
    url = f"http://suggest3.sinajs.cn/suggest/type=&key={keyword}"
    try:
        headers = {'Referer': 'http://finance.sina.com.cn'}
        resp = requests.get(url, headers=headers, timeout=2)
        content = resp.text
        # var suggestvalue="黄金,87,au0,au0,黄金,,黄金,99,1,,,;..."
        if '="' not in content:
            return []
            
        data_str = content.split('="')[1].strip('"')
        if not data_str:
            return []
            
        results = []
        items = data_str.split(';')
        for item in items:
            parts = item.split(',')
            if len(parts) >= 4:
                # format: name, type, code_short, code_full, ...
                # e.g. 黄金ETF, 203, 518880, sh518880, ...
                name = parts[0]
                code_full = parts[3]
                
                # 简单的过滤：只保留 sz/sh 开头的股票/基金
                if code_full.startswith('sz') or code_full.startswith('sh'):
                    results.append((code_full, name))
                    
        return results
    except Exception as e:
        print(f"Search error: {e}")
        return []

def update_ui_loop():
    """
    后台线程：循环获取数据并更新UI
    """
    global root, labels
    while True:
        try:
            if not root or not root.winfo_exists():
                break
                
            data_map = get_stock_data_tencent(STOCKS)
            
            # 确保labels数量与STOCKS一致
            # 在主线程中更新UI组件
            root.after(0, lambda: refresh_labels(data_map))
            
        except Exception as e:
            pass
            
        time.sleep(REFRESH_RATE)

def shake_window():
    """窗口抖动动画"""
    if not root: return
    
    original_x = root.winfo_x()
    original_y = root.winfo_y()
    
    # 抖动参数
    intensity = 10 # 幅度加大
    steps = 15     # 次数增加
    
    for _ in range(steps):
        dx = random.randint(-intensity, intensity)
        dy = random.randint(-intensity, intensity)
        root.geometry(f"+{original_x+dx}+{original_y+dy}")
        root.update()
        time.sleep(0.02) # 20ms
        
    # 恢复原位
    root.geometry(f"+{original_x}+{original_y}")

# 全局变量用于缓存UI组件，避免重复创建
main_frame = None
stock_row_widgets = []
last_display_mode = None
last_stock_count = 0
last_show_price = None
last_show_volume = None

def bind_events(widget):
    """绑定通用事件到组件"""
    widget.bind("<Button-1>", start_drag)
    widget.bind("<B1-Motion>", on_drag)
    widget.bind("<Button-3>", show_context_menu)
    widget.bind("<Double-Button-1>", minimize_window)

def get_trading_minutes():
    """计算当前已交易分钟数 (0-240)"""
    now = datetime.now()
    start_am = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end_am = now.replace(hour=11, minute=30, second=0, microsecond=0)
    start_pm = now.replace(hour=13, minute=0, second=0, microsecond=0)
    end_pm = now.replace(hour=15, minute=0, second=0, microsecond=0)

    if now < start_am: return 0
    if now >= end_pm: return 240

    minutes = 0
    if now >= start_am:
        if now <= end_am:
            minutes = (now - start_am).total_seconds() / 60
        else:
            minutes = 120 # Full morning

    if now >= start_pm:
        minutes += (now - start_pm).total_seconds() / 60
    
    return min(minutes, 240)

def refresh_labels(data_map):
    """在主线程刷新Labels (重构版：支持Grid布局)"""
    global main_frame, stock_row_widgets, last_display_mode, last_stock_count, root, last_percentages
    global session_max_map, current_date_str, show_price, last_show_price, show_volume, last_show_volume
    
    if not root: return
    
    # 检查日期变更 (处理跨天运行的情况)
    today = datetime.now().strftime("%Y-%m-%d")
    if today != current_date_str:
        current_date_str = today
        session_max_map = {} # 新的一天，重置历史最大值
        save_config() # 更新配置文件中的日期
    
    # 初始化主容器
    if main_frame is None:
        main_frame = tk.Frame(root, bg="black")
        main_frame.pack(fill="both", expand=True)
        bind_events(main_frame) # 允许拖动背景
        
    # 检查是否需要重建布局
    # 条件：模式改变 或 股票数量改变 或 价格显示设置改变 或 成交量显示改变
    need_rebuild = (display_mode != last_display_mode) or \
                   (len(STOCKS) != last_stock_count) or \
                   (show_price != last_show_price) or \
                   (show_volume != last_show_volume)
    
    if need_rebuild:
        # 清除旧组件
        for widget in main_frame.winfo_children():
            widget.destroy()
        stock_row_widgets = []
        
        # 重建布局
        for i, stock in enumerate(STOCKS):
            row_widgets = {}
            col_idx = 0
            
            # 1. 名称 (所有模式都有)
            name_label = tk.Label(main_frame, text=stock['name'], bg="black", fg="white", 
                                 font=FONT_CONFIG, anchor="w")
            name_label.grid(row=i, column=col_idx, sticky="nswe", padx=(10, 5), pady=2)
            bind_events(name_label)
            row_widgets['name'] = name_label
            col_idx += 1
            
            # 2. 价格 (可选)
            if show_price:
                price_label = tk.Label(main_frame, text="--", bg="black", fg="white",
                                     font=FONT_CONFIG, anchor="e")
                price_label.grid(row=i, column=col_idx, sticky="nswe", padx=(5, 5), pady=2)
                bind_events(price_label)
                row_widgets['price'] = price_label
                col_idx += 1
            
            if display_mode == "bar":
                # 3. 柱状图 (Canvas)
                # 增加宽度到 150px，提升显示精度
                bar_canvas = tk.Canvas(main_frame, bg="black", height=24, width=150, highlightthickness=0)
                bar_canvas.grid(row=i, column=col_idx, sticky="nswe", padx=5, pady=2)
                bind_events(bar_canvas)
                row_widgets['bar'] = bar_canvas
                col_idx += 1
                
                # 4. 百分比
                pct_label = tk.Label(main_frame, text="--%", bg="black", fg="white",
                                    font=("Microsoft YaHei UI", 10, "bold"), anchor="e")
                pct_label.grid(row=i, column=col_idx, sticky="nswe", padx=(5, 10), pady=2)
                bind_events(pct_label)
                row_widgets['pct'] = pct_label
                col_idx += 1
                
            else: # percent mode
                # 3. 百分比 (直接放在下一列)
                pct_label = tk.Label(main_frame, text="--%", bg="black", fg="white",
                                    font=FONT_CONFIG, anchor="e")
                pct_label.grid(row=i, column=col_idx, sticky="nswe", padx=(20, 10), pady=2) # 增加左侧间距实现"双列对齐"
                bind_events(pct_label)
                row_widgets['pct'] = pct_label
                col_idx += 1
            
            # 5. 成交量 (可选，放在最后)
            if show_volume:
                vol_label = tk.Label(main_frame, text="", bg="black", fg="white",
                                   font=FONT_CONFIG, anchor="w") # 左对齐
                vol_label.grid(row=i, column=col_idx, sticky="nswe", padx=(5, 10), pady=2)
                bind_events(vol_label)
                row_widgets['vol'] = vol_label
                col_idx += 1
                
            stock_row_widgets.append(row_widgets)
            
        last_display_mode = display_mode
        last_stock_count = len(STOCKS)
        last_show_price = show_price
        last_show_volume = show_volume
        
        # 配置列权重
        # 无论多少列，最后一列（百分比）通常需要一点权重，或者名称列自适应
        total_cols = col_idx
        for c in range(total_cols):
             main_frame.grid_columnconfigure(c, weight=0) # 默认不拉伸
        
        # 只有在百分比模式下，可能希望某些列拉伸填满
        # 但为了紧凑，通常都设为0，由窗口大小决定? 
        # 这里维持原逻辑：bar模式下都不拉伸，percent模式下最后一列拉伸
        if display_mode == "percent":
             main_frame.grid_columnconfigure(total_cols-1, weight=1) 
            
    # === 更新数据 ===
    
    # 1. 更新每只股票的历史最大值 (Session Max)
    for code in data_map:
        # 兼容 (price, percent) 和 (price, percent, volume)
        val = data_map[code]
        percent = val[1]
        
        cur_abs = abs(percent)
        if cur_abs > session_max_map.get(code, 0.0):
            session_max_map[code] = cur_abs
            
    # 2. 计算全局视口上限 (View Ceiling)
    # 取所有当前监控股票中的最大历史波动，作为统一的缩放基准
    # 这样可以保证不同股票的柱状图长度是可比的 (例如: 1%的长度在所有行都一样)
    current_max_all = 0.0
    for stock in STOCKS:
        code = stock['code']
        # 即使股票不在当前data_map中(可能网络问题)，也应保留其历史最大值记录
        m = session_max_map.get(code, 0.0)
        if m > current_max_all:
            current_max_all = m
            
    # 规则: 
    # 1. 至少显示 2.5% 的范围 (降低默认阈值，让日常 0.x%~1% 的波动看起来更明显)
    # 2. 如果全局历史最大值超过 2.5%，则视口跟随扩张 (兼容大行情)
    view_ceiling = max(2.5, current_max_all)
    
    should_shake = False
    
    for i, stock in enumerate(STOCKS):
        if i >= len(stock_row_widgets): break
        
        widgets = stock_row_widgets[i]
        code = stock['code']
        display_name = stock['name']
        if len(display_name) > 8: display_name = display_name[:8]
        
        # 默认颜色
        color = "#cccccc"
        percent = 0.0
        current_price = 0.0
        vol_text = ""
        
        if code in data_map:
            val = data_map[code]
            volume = 0
            if len(val) == 3:
                current_price, percent, volume = val
            else:
                current_price, percent = val
            
            color = "#ff3333" if percent > 0 else "#00cc00"
            if percent == 0: color = "#cccccc"
            
            # 成交量分析 (放量/缩量)
            # 只有在开盘期间或收盘后才计算
            mins = get_trading_minutes()
            if show_volume and mins > 5 and code in MA5_VOLUMES: # 开盘5分钟后再看，避免初始波动
                ma5_vol = MA5_VOLUMES[code]
                if ma5_vol > 0:
                    # 预测今日全天成交量
                    proj_vol = (volume / mins) * 240
                    ratio = proj_vol / ma5_vol
                    
                    # 显示量比数值
                    vol_text = f"{ratio:.1f}x"
                    
                    # 调整阈值 (基于网络调研：1.5倍以上即为明显放量，0.6以下为明显缩量)
                    if ratio > 1.5: # 放量 (原2.0太难触发)
                        vol_text += "🔥"
                    elif ratio < 0.6: # 缩量 (原0.5太难触发)
                        vol_text += "❄️"
                    else:
                        vol_text += "📊"
            
            # 抖动检测
            if code in last_percentages:
                prev_percent = last_percentages[code]
                if (prev_percent >= 0 and percent < 0) or (prev_percent <= 0 and percent > 0):
                    should_shake = True
                if int(abs(percent)) > int(abs(prev_percent)):
                    should_shake = True
            last_percentages[code] = percent
        
        # 更新名称
        widgets['name'].config(text=display_name, fg=color)
        
        # 更新价格
        if 'price' in widgets:
            price_text = f"{current_price:.3f}" if code in data_map else "--"
            widgets['price'].config(text=price_text, fg=color)
        
        # 更新百分比
        pct_text = f"{percent:+.2f}%" if code in data_map else "--"
        widgets['pct'].config(text=pct_text, fg=color)
        
        # 更新成交量
        if 'vol' in widgets:
            widgets['vol'].config(text=vol_text, fg=color)
        
        # 更新柱状图 (如果存在)
        if 'bar' in widgets:
            canvas = widgets['bar']
            canvas.delete("all")
            
            # 只有有数据时才画
            if code in data_map:
                w = canvas.winfo_width()
                if w < 10: w = 150 # 初始可能未渲染，取默认
                h = canvas.winfo_height()
                if h < 10: h = 24
                
                # 居中绘制
                center_x = w / 2
                center_y = h / 2
                
                # === 绘制边界括号 (类似【】效果) ===
                bracket_color = "#555555" # 深灰色边框
                bracket_h = 14 # 括号高度
                bracket_w = 3  # 括号勾的宽度
                margin_x = 4   # 距离边缘距离
                
                y_top = center_y - (bracket_h / 2)
                y_bottom = center_y + (bracket_h / 2)
                
                # 左括号 [
                lx = margin_x
                canvas.create_line(lx, y_top, lx, y_bottom, fill=bracket_color, width=2)
                canvas.create_line(lx, y_top, lx+bracket_w, y_top, fill=bracket_color, width=2)
                canvas.create_line(lx, y_bottom, lx+bracket_w, y_bottom, fill=bracket_color, width=2)
                
                # 右括号 ]
                rx = w - margin_x
                canvas.create_line(rx, y_top, rx, y_bottom, fill=bracket_color, width=2)
                canvas.create_line(rx, y_top, rx-bracket_w, y_top, fill=bracket_color, width=2)
                canvas.create_line(rx, y_bottom, rx-bracket_w, y_bottom, fill=bracket_color, width=2)
                
                # === 计算柱状图 (在括号内部) ===
                # 左右各预留 12px 给括号和空隙
                draw_w = w - 24
                if draw_w < 10: draw_w = 10
                
                # 1. 灰色轨道长度
                this_stock_max = session_max_map.get(code, 0.0)
                track_len = (this_stock_max / view_ceiling) * draw_w
                if track_len > draw_w: track_len = draw_w
                if track_len < 4: track_len = 4 # 最小长度

                # 2. 彩色柱子长度
                bar_len = (abs(percent) / view_ceiling) * draw_w
                if bar_len > draw_w: bar_len = draw_w
                if bar_len < 2: bar_len = 2 # 最小长度
                
                # 颜色定义
                bar_color = "#FF4D4F" if percent > 0 else "#52C41A" # 现代红绿
                if percent == 0: bar_color = "#999999"
                track_color = "#333333" # 轨道底色
                
                # 绘制轨道 (圆角背景)
                line_width = 8 # 柱子粗细
                
                track_x1 = center_x - (track_len / 2)
                track_x2 = center_x + (track_len / 2)
                
                canvas.create_line(track_x1, center_y, track_x2, center_y, 
                                  width=line_width, fill=track_color, capstyle=tk.ROUND)
                
                # 绘制当前值 (圆角前景)
                bar_x1 = center_x - (bar_len / 2)
                bar_x2 = center_x + (bar_len / 2)
                
                # 确保最小长度能看清圆角
                if bar_len < line_width: 
                    bar_x1 = center_x
                    bar_x2 = center_x
                
                canvas.create_line(bar_x1, center_y, bar_x2, center_y,
                                  width=line_width, fill=bar_color, capstyle=tk.ROUND)

    # 动态调整窗口大小
    main_frame.update_idletasks() # 强制计算布局
    req_width = main_frame.winfo_reqwidth()
    req_height = main_frame.winfo_reqheight()
    
    # 增加一点padding
    target_width = req_width
    target_height = req_height
    
    current_width = root.winfo_width()
    current_height = root.winfo_height()
    
    # 只有差异大时才调整，防止抖动
    if abs(target_width - current_width) > 5 or abs(target_height - current_height) > 5:
        root.geometry(f"{target_width}x{target_height}+{root.winfo_x()}+{root.winfo_y()}")

    if should_shake:
        root.after(50, shake_window)

def start_drag(event):
    root_win = event.widget.winfo_toplevel()
    root_win.x = event.x
    root_win.y = event.y

def on_drag(event):
    root_win = event.widget.winfo_toplevel()
    # 计算相对于屏幕的移动偏移量
    # 注意：event.x 是相对于组件的坐标，不能直接用差值加到root位置
    # 正确的做法是记录点击位置相对于root左上角的偏移，或者每次移动计算deltas
    # 这里原来的逻辑是: deltax = event.x - start_x. 
    # 如果start_x是相对于widget的，那么event.x也是。差值就是移动量。
    deltax = event.x - root_win.x
    deltay = event.y - root_win.y
    x = root_win.winfo_x() + deltax
    y = root_win.winfo_y() + deltay
    root_win.geometry(f"+{x}+{y}")

def toggle_display_mode(mode):
    """切换显示模式"""
    global display_mode
    display_mode = mode
    save_config()
    # 立即触发刷新
    if root: root.after(0, lambda: refresh_labels({}))

def toggle_show_price():
    """切换是否显示价格"""
    global show_price
    show_price = not show_price
    save_config()
    # 立即触发刷新
    if root: root.after(0, lambda: refresh_labels({}))

def toggle_show_volume():
    """切换是否显示成交量"""
    global show_volume
    show_volume = not show_volume
    save_config()
    # 立即触发刷新
    if root: root.after(0, lambda: refresh_labels({}))

def quit_app():
    """退出程序，解决残留白框问题"""
    global root
    if root:
        try:
            root.withdraw() # 先隐藏窗口
            root.quit()     # 停止主循环
            root.destroy()  # 销毁窗口
        except Exception:
            pass

# ================= AI技术分析模块 =================
def get_kline_data_analysis(code):
    """获取K线数据 (用于技术分析)"""
    # 简单的代码转换
    api_code = code
    if code.startswith("csi"): api_code = "sh" + code[3:]
    elif code.startswith("sh1b"): api_code = "sh00" + code[4:]
    
    # 不支持非股票/指数代码
    if code.startswith(("hf_", "gds_", "nf_", "Au", "Ag", "Pt")):
        return None

    # 获取100天日K
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={api_code},day,,,100,qfq"
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code != 200: return None
        data = resp.json()
        
        stock_data = data['data'].get(api_code, {})
        # 兼容不同字段名
        kline = stock_data.get('qfqday', stock_data.get('day', []))
        
        parsed_data = []
        for item in kline:
            # item: [date, open, close, high, low, volume, ...]
            parsed_data.append({
                "date": item[0],
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "volume": float(item[5])
            })
        return parsed_data
    except Exception as e:
        print(f"Analysis Error: {e}")
        return None

def calculate_ma(data, days):
    """计算移动平均线"""
    if len(data) < days:
        return None
    
    # 取最后N天
    subset = data[-days:]
    avg = sum(d['close'] for d in subset) / days
    return avg

def calculate_rsi(data, periods=14):
    """计算RSI相对强弱指标"""
    if len(data) < periods + 1:
        return None
        
    gains = []
    losses = []
    
    # 计算每日涨跌
    for i in range(1, len(data)):
        change = data[i]['close'] - data[i-1]['close']
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
            
    # 只取最近N天用于计算初始值 (简单算法)
    # 标准RSI需要平滑移动平均，这里用简单平均模拟近似值
    recent_gains = gains[-periods:]
    recent_losses = losses[-periods:]
    
    avg_gain = sum(recent_gains) / periods
    avg_loss = sum(recent_losses) / periods
    
    if avg_loss == 0:
        return 100
        
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(data, short=12, long=26, mid=9):
    """计算MACD (DIF, DEA, MACD)"""
    if not data or len(data) < long + mid: return None
    
    # 简单EMA计算
    closes = [d['close'] for d in data]
    
    def get_ema(values, n):
        ema = [values[0]]
        alpha = 2 / (n + 1)
        for i in range(1, len(values)):
            ema.append(alpha * values[i] + (1 - alpha) * ema[-1])
        return ema
        
    ema_short = get_ema(closes, short)
    ema_long = get_ema(closes, long)
    
    dif = [s - l for s, l in zip(ema_short, ema_long)]
    dea = get_ema(dif, mid)
    
    macd_bar = [(d - de) * 2 for d, de in zip(dif, dea)]
    
    return {
        "dif": dif[-1],
        "dea": dea[-1],
        "macd": macd_bar[-1],
        "prev_macd": macd_bar[-2]
    }

def calculate_kdj(data, n=9, m1=3, m2=3):
    """计算KDJ"""
    if not data or len(data) < n: return None
    
    k_val = 50
    d_val = 50
    
    # 需要逐日计算以获得准确序列，这里简化计算最后几天
    # 为了准确性，我们从足够早的地方开始算
    start_idx = max(0, len(data) - 100) 
    
    for i in range(start_idx, len(data)):
        # 获取过去N天(含今天)的最高最低
        window = data[max(0, i-n+1):i+1]
        if not window: continue
        
        low_n = min(d['low'] for d in window)
        high_n = max(d['high'] for d in window)
        close = data[i]['close']
        
        if high_n == low_n:
            rsv = 50
        else:
            rsv = (close - low_n) / (high_n - low_n) * 100
            
        k_val = (m1-1)/m1 * k_val + 1/m1 * rsv
        d_val = (m2-1)/m2 * d_val + 1/m2 * k_val
        
    j_val = 3 * k_val - 2 * d_val
    
    return {"k": k_val, "d": d_val, "j": j_val}

def generate_analysis_data(code, name):
    """生成分析数据字典 (分离数据与视图)"""
    data = get_kline_data_analysis(code)
    if not data: return None
    
    current_price = data[-1]['close']
    yesterday_price = data[-2]['close']
    
    # 1. 趋势分析
    ma5 = calculate_ma(data, 5)
    ma20 = calculate_ma(data, 20)
    ma60 = calculate_ma(data, 60)
    macd = calculate_macd(data)
    
    # 2. 资金分析
    vol_today = data[-1]['volume']
    vol_ma5 = 0
    if len(data) >= 6:
        vol_ma5 = sum(d['volume'] for d in data[-6:-1]) / 5
    
    # 3. 情绪分析
    rsi = calculate_rsi(data)
    kdj = calculate_kdj(data)
    
    # 4. 综合研判打分
    score = 0
    
    # 趋势分 (3分)
    trend_desc = "震荡"
    
    # 细化趋势描述与评分
    if ma5 and ma20:
        if ma5 > ma20:
            if current_price > ma20:
                trend_desc = "多头排列"
                score += 1.0
            else:
                trend_desc = "回调震荡"
                score -= 0.5 # 破位风险
        else: # ma5 < ma20
            if current_price > ma20:
                trend_desc = "反弹震荡"
                score += 0.5 # 弱势反弹
            else:
                trend_desc = "空头排列"
                score -= 1.0
    elif ma20:
        # 只有MA20的情况 (新股或数据不足)
        if current_price > ma20: 
            score += 0.5
            trend_desc = "站上均线"
        else: 
            score -= 0.5
            trend_desc = "均线压制"

    # 均线交叉评分 (额外加分项)
    if ma5 and ma20:
        if ma5 > ma20: score += 0.5
        elif ma5 < ma20: score -= 0.5
    
    if macd:
        # MACD 金叉/死叉
        if macd['dif'] > macd['dea']: score += 0.5 # 金叉状态
        elif macd['dif'] < macd['dea']: score -= 0.5 # 死叉状态
        
        if macd['macd'] > 0 and macd['macd'] > macd['prev_macd']: score += 0.5 # 红柱增长
        if macd['dif'] > 0 and macd['dea'] > 0: score += 0.5 # 零轴上方
    
    # 资金分 (2分)
    vol_desc = "平量"
    vol_ratio = 0
    if vol_ma5 > 0:
        vol_ratio = vol_today / vol_ma5
        if vol_ratio > 1.5: 
            vol_desc = "放量"
            if current_price > yesterday_price: score += 1 # 放量涨
            else: score -= 1 # 放量跌
        elif vol_ratio < 0.6: 
            vol_desc = "缩量"
            if current_price < yesterday_price: score += 0.5 # 缩量跌(惜售)
            elif current_price > yesterday_price: score -= 0.5 # 缩量涨(背离风险)
        
        # 补充逻辑：如果放量过大 (>3.0) 且在高位，可能是出货，扣分
        if vol_ratio > 3.0 and current_price > ma20:
             score -= 0.5
            
    # 情绪分 (2分)
    sentiment_desc = "中性"
    if rsi:
        if rsi > 80: 
            score -= 1
            sentiment_desc = "超买"
        elif rsi < 20: 
            score += 1.5 # 超卖反弹权重高
            sentiment_desc = "超卖"
            
    if kdj:
        # KDJ 金叉/死叉
        if kdj['k'] > kdj['d']: score += 0.5 # 金叉
        elif kdj['k'] < kdj['d']: score -= 0.5 # 死叉
        
        if kdj['j'] < 0 or kdj['j'] > 100:
             if kdj['j'] < 0: score += 0.5
             if kdj['j'] > 100: score -= 1.0 # J值过高风险极大

             
    # 结论
    conclusion = "观察"
    action_color = "#888888" # Gray
    if score >= 2.5:
        conclusion = "积极买入"
        action_color = "#FF4D4F" # Red
    elif score >= 1:
        conclusion = "持有/低吸"
        action_color = "#FF7875" # Light Red
    elif score <= -1.5:
        conclusion = "减仓/卖出"
        action_color = "#52C41A" # Green
    elif score <= 0:
        conclusion = "观望"
        action_color = "#95DE64" # Light Green
        
    return {
        "name": name,
        "code": code,
        "price": current_price,
        "pct": (current_price - yesterday_price) / yesterday_price * 100,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "macd": macd,
        "vol_ratio": vol_ratio,
        "vol_desc": vol_desc,
        "trend_desc": trend_desc,
        "sentiment_desc": sentiment_desc,
        "rsi": rsi,
        "kdj": kdj,
        "conclusion": conclusion,
        "action_color": action_color,
        "score": score
    }

def show_analysis_result(name, stock_info=None):
    """显示分析结果窗口 (美化版)"""
    # stock_info 是 generate_analysis_data 的返回值
    # 如果没传，说明是旧调用，需要重新获取 (线程安全)
    if not stock_info:
         # 兼容旧代码，理论上 run_analysis_thread 应该改一下，这里先不处理，依赖线程传参
         pass

    # 创建Toplevel
    top = tk.Toplevel(root)
    top.title(f"技术面分析 - {stock_info['name']}")
    top.overrideredirect(True) # 无边框模式，自己画标题栏
    top.attributes("-topmost", True) # 确保置顶，防止被误认为关闭
    
    # === 窗口尺寸与定位 (侧边弹出) ===
    width = 540 # 增加宽度以容纳长数字
    height = 700
    
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    root_x = root.winfo_x()
    root_y = root.winfo_y()
    root_w = root.winfo_width()
    
    # 默认放右边
    pos_x = root_x + root_w + 10
    # 如果右边放不下，放左边
    if pos_x + width > screen_w:
        pos_x = root_x - width - 10
        
    # 纵向位置对齐
    pos_y = root_y
    if pos_y + height > screen_h:
        pos_y = screen_h - height - 10
        
    top.geometry(f"{width}x{height}+{int(pos_x)}+{int(pos_y)}")
    top.configure(bg="#1E1E1E") # 深色背景
    
    # === 自定义标题栏 ===
    title_frame = tk.Frame(top, bg="#2D2D2D", height=30)
    title_frame.pack(fill="x")
    
    # 允许拖动
    def start_move(event):
        top.x = event.x
        top.y = event.y
    def do_move(event):
        deltax = event.x - top.x
        deltay = event.y - top.y
        x = top.winfo_x() + deltax
        y = top.winfo_y() + deltay
        top.geometry(f"+{x}+{y}")
    
    title_frame.bind("<Button-1>", start_move)
    title_frame.bind("<B1-Motion>", do_move)
    
    title_label = tk.Label(title_frame, text=f"📊 {stock_info['name']} ({stock_info['code']})", 
             bg="#2D2D2D", fg="white", font=("Microsoft YaHei UI", 10, "bold"))
    title_label.pack(side="left", padx=10)
    # 绑定 Label 的拖动事件，解决点击文字无法拖动的问题
    title_label.bind("<Button-1>", start_move)
    title_label.bind("<B1-Motion>", do_move)
             
    # 关闭按钮
    close_btn = tk.Label(title_frame, text="✕", bg="#2D2D2D", fg="#888888", font=("Arial", 12), cursor="hand2")
    close_btn.pack(side="right", padx=10)
    close_btn.bind("<Button-1>", lambda e: top.destroy())
    close_btn.bind("<Enter>", lambda e: close_btn.config(fg="white"))
    close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#888888"))
    content_frame = tk.Frame(top, bg="#1E1E1E", padx=15, pady=15)
    content_frame.pack(fill="both", expand=True)
    
    # 1. 头部价格
    price_color = "#FF4D4F" if stock_info['pct'] >= 0 else "#52C41A"
    header_frame = tk.Frame(content_frame, bg="#1E1E1E")
    header_frame.pack(fill="x", pady=(0, 15))
    
    tk.Label(header_frame, text=f"{stock_info['price']:.2f}", font=("Arial", 24, "bold"), 
             bg="#1E1E1E", fg=price_color).pack(side="left")
    tk.Label(header_frame, text=f"{stock_info['pct']:+.2f}%", font=("Arial", 14), 
             bg="#1E1E1E", fg=price_color).pack(side="left", padx=10, pady=(8, 0))
             
    # 2. 结论卡片 (高亮)
    con_frame = tk.Frame(content_frame, bg=stock_info['action_color'], padx=2, pady=2) # 边框色
    con_frame.pack(fill="x", pady=(0, 15))
    con_inner = tk.Frame(con_frame, bg="#252526", padx=10, pady=10)
    con_inner.pack(fill="both", expand=True)
    
    tk.Label(con_inner, text="技术综合研判", font=("Microsoft YaHei UI", 9), bg="#252526", fg="#AAAAAA").pack(anchor="w")
    
    # 结论行容器，用于水平排列结论和分数
    con_row = tk.Frame(con_inner, bg="#252526")
    con_row.pack(fill="x", pady=5)
    
    tk.Label(con_row, text=stock_info['conclusion'], font=("Microsoft YaHei UI", 16, "bold"), 
             bg="#252526", fg=stock_info['action_color']).pack(side="left", expand=True) # 居中
             
    # 右下角显示分数
    tk.Label(con_inner, text=f"Score: {stock_info['score']:.1f}", font=("Arial", 8), bg="#252526", fg="#666666").pack(anchor="e")
             
    # 3. 指标网格
    grid_frame = tk.Frame(content_frame, bg="#1E1E1E")
    grid_frame.pack(fill="both", expand=True)
    
    def create_metric_row(parent, label, value, sub_value, status_color="#FFFFFF"):
        row = tk.Frame(parent, bg="#1E1E1E", pady=3)
        row.pack(fill="x")
        # 调整列宽权重
        row.columnconfigure(1, weight=1)
        
        tk.Label(row, text=label, font=("Microsoft YaHei UI", 10), bg="#1E1E1E", fg="#888888", width=10, anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(row, text=value, font=("Arial", 10, "bold"), bg="#1E1E1E", fg="white").grid(row=0, column=1, sticky="w", padx=5)
        tk.Label(row, text=sub_value, font=("Microsoft YaHei UI", 9), bg="#1E1E1E", fg=status_color).grid(row=0, column=2, sticky="e")
        return row

    # 趋势
    tk.Label(grid_frame, text="📈 趋势分析", font=("Microsoft YaHei UI", 10, "bold"), bg="#1E1E1E", fg="#CCCCCC").pack(anchor="w", pady=(5,5))
    
    trend_color = "#FF4D4F" if "多" in stock_info['trend_desc'] else "#52C41A"
    create_metric_row(grid_frame, "均线状态", stock_info['trend_desc'], 
                     f"MA5: {stock_info['ma5']:.2f}  MA20: {stock_info['ma20']:.2f}" if stock_info['ma20'] else "--", trend_color)
                     
    macd_val = stock_info['macd']['macd'] if stock_info['macd'] else 0
    # MACD 描述优化
    if stock_info['macd']:
        dif = stock_info['macd']['dif']
        dea = stock_info['macd']['dea']
        macd_state = "金叉" if dif > dea else "死叉"
        macd_color = "#FF4D4F" if dif > dea else "#52C41A"
    else:
        macd_state = "--"
        macd_color = "#888888"
        
    create_metric_row(grid_frame, "MACD信号", macd_state, f"DIF: {stock_info['macd']['dif']:.3f}" if stock_info['macd'] else "--", macd_color)

    tk.Frame(grid_frame, height=1, bg="#333333").pack(fill="x", pady=8) # 分割线

    # 资金
    tk.Label(grid_frame, text="💰 资金分析", font=("Microsoft YaHei UI", 10, "bold"), bg="#1E1E1E", fg="#CCCCCC").pack(anchor="w", pady=(5,5))
    
    vol_color = "#FF4D4F" if stock_info['vol_ratio'] > 1.5 else ("#52C41A" if stock_info['vol_ratio'] < 0.6 else "white")
    create_metric_row(grid_frame, "量比", f"{stock_info['vol_ratio']:.2f}", stock_info['vol_desc'], vol_color)

    tk.Frame(grid_frame, height=1, bg="#333333").pack(fill="x", pady=8) # 分割线
    
    # 情绪
    tk.Label(grid_frame, text="🌡️ 情绪分析", font=("Microsoft YaHei UI", 10, "bold"), bg="#1E1E1E", fg="#CCCCCC").pack(anchor="w", pady=(5,5))
    
    rsi_val = stock_info['rsi'] if stock_info['rsi'] else 0
    rsi_color = "#FF4D4F" if rsi_val > 80 else ("#52C41A" if rsi_val < 20 else "white")
    create_metric_row(grid_frame, "RSI(14)", f"{rsi_val:.1f}", stock_info['sentiment_desc'], rsi_color)
    
    kdj_j = stock_info['kdj']['j'] if stock_info['kdj'] else 0
    
    # KDJ 信号展示
    if stock_info['kdj']:
        k = stock_info['kdj']['k']
        d = stock_info['kdj']['d']
        kdj_signal = "金叉" if k > d else "死叉"
        kdj_color = "#FF4D4F" if k > d else "#52C41A"
    else:
        kdj_signal = "--"
        kdj_color = "#888888"
        
    create_metric_row(grid_frame, "KDJ信号", kdj_signal, f"J: {kdj_j:.1f}", kdj_color)
    
    # 免责声明
    tk.Label(content_frame, text="⚠️ 本工具分析结果仅供个人娱乐，不构成任何投资建议", 
             font=("Microsoft YaHei UI", 8), bg="#1E1E1E", fg="#555555").pack(side="bottom", pady=10)


def run_analysis_thread(stock):
    """在线程中运行分析"""
    def task():
        # 获取结构化数据
        data = generate_analysis_data(stock['code'], stock['name'])
        if data:
            root.after(0, lambda: show_analysis_result(stock['name'], data))
        else:
            # 错误处理
            pass
        
    threading.Thread(target=task, daemon=True).start()

def show_context_menu(event):
    """显示右键菜单"""
    menu = tk.Menu(root, tearoff=0)
    
    # 查找点击的是哪个股票
    clicked_stock = None
    if stock_row_widgets:
        for i, row in enumerate(stock_row_widgets):
            # 检查点击的组件是否在当前行的组件列表中
            if event.widget in row.values():
                clicked_stock = STOCKS[i]
                break
                
    if clicked_stock:
        # 添加分析选项 (仅对股票/指数有效)
        code = clicked_stock['code']
        # 简单过滤掉明显不支持的品种 (如黄金、外汇的前缀)
        if not code.startswith(("hf_", "gds_", "nf_")):
            menu.add_command(label=f"📈 技术面分析: {clicked_stock['name']}", 
                            command=lambda s=clicked_stock: run_analysis_thread(s))
            menu.add_separator()
    
    # 显示模式子菜单
    mode_menu = tk.Menu(menu, tearoff=0)
    mode_menu.add_radiobutton(label="纯百分比 (Percent)", command=lambda: toggle_display_mode("percent"))
    mode_menu.add_radiobutton(label="柱状图 (Bar Chart)", command=lambda: toggle_display_mode("bar"))
    # 设置当前选中项 (Radiobutton需要variable才能同步显示选中状态，这里简化处理，只提供功能)
    
    menu.add_cascade(label="显示模式 (Display Mode)", menu=mode_menu)
    
    # 显示价格开关
    price_label = "隐藏价格 (Hide Price)" if show_price else "显示价格 (Show Price)"
    menu.add_command(label=price_label, command=toggle_show_price)
    
    # 显示成交量开关
    vol_label = "隐藏成交量 (Hide Volume)" if show_volume else "显示成交量 (Show Volume)"
    menu.add_command(label=vol_label, command=toggle_show_volume)
    
    menu.add_separator()
    menu.add_command(label="配置股票", command=open_settings)
    menu.add_separator()
    menu.add_command(label="退出 (Exit)", command=quit_app)
    
    # 使用 tk_popup 替代 post，通常能更好地处理自动关闭
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        # 确保释放抓取，防止菜单卡死
        menu.grab_release()

def open_settings():
    """打开设置窗口"""
    
    settings_win = tk.Toplevel(root)
    settings_win.title("配置股票")
    
    # === 计算显示位置：在悬浮窗正右方 ===
    try:
        root_x = root.winfo_x()
        root_y = root.winfo_y()
        root_w = root.winfo_width()
        
        # 目标位置
        pos_x = root_x + root_w + 10
        pos_y = root_y
        
        # 确保不超出屏幕太远 (简单判断)
        screen_w = root.winfo_screenwidth()
        if pos_x + 700 > screen_w:
             # 如果右边放不下，就放左边
             pos_x = root_x - 700 - 10
             if pos_x < 0: pos_x = 10 # 实在放不下就放最左边
        
        settings_win.geometry(f"700x800+{pos_x}+{pos_y}")
    except Exception:
        settings_win.geometry("700x800") # 降级处理

    
    # === 预设添加 (重构：分级/多分类) ===
    preset_frame = tk.LabelFrame(settings_win, text="快速添加预设 (常用指数/商品)", padx=5, pady=5)
    preset_frame.pack(fill="x", padx=5, pady=5)
    
    # 预设数据字典
    preset_categories = {
        "金价": {
            "国际金价": ("hf_XAU", "国际金价"),
            "国内金价": ("gds_AU9999", "国内金价"),
        },
        "A股指数": {
            "上证指数": ("sh000001", "上证指数"),
            "深证成指": ("sz399001", "深证成指"),
            "创业板指": ("sz399006", "创业板指"),
            "科创50": ("sh000688", "科创50"),
            "沪深300": ("sh000300", "沪深300"),
            "中证500": ("sh000905", "中证500"),
            "北证50": ("bj899050", "北证50"),
        },
        "港股指数": {
            "恒生指数": ("hkHSI", "恒生指数"),
            "恒生科技": ("hkHSTECH", "恒生科技"),
            "国企指数": ("hkHSCEI", "国企指数"),
        },
        "美股": {
            "道琼斯": ("us.DJI", "道琼斯"),
            "纳斯达克": ("us.IXIC", "纳斯达克"),
            "标普500": ("us.INX", "标普500"),
        }
    }
    
    # 定义确认函数 (改为直接添加)
    def on_preset_add():
        cat = cat_var.get()
        item_key = item_var.get()
        if cat in preset_categories and item_key in preset_categories[cat]:
            code, name = preset_categories[cat][item_key]
            
            # 填入下方的编辑框 (方便用户查看)
            code_entry.delete(0, tk.END)
            code_entry.insert(0, code)
            name_entry.delete(0, tk.END)
            name_entry.insert(0, name)
            
            # 检查是否已存在
            for s in STOCKS:
                if s["code"] == code:
                    messagebox.showinfo("提示", f"{name} ({code}) 已在列表中")
                    return

            # 添加到列表
            STOCKS.append({"code": code, "name": name})
            save_config()
            refresh_list()
            # messagebox.showinfo("成功", f"已添加 {name} 到监控列表") # 用户要求不弹窗

    # 布局调整：使用 Frame + Pack 布局 (仿照搜索框样式)，恢复使用 ttk.Combobox (样式更好看)
    # 并确保 Pack 布局正确
    
    # 顶部输入行容器
    input_frame = tk.Frame(preset_frame)
    input_frame.pack(fill="x", pady=5)
    
    # 1. 分类
    tk.Label(input_frame, text="分类:").pack(side="left", padx=5)
    
    cat_var = tk.StringVar()
    cat_choices = list(preset_categories.keys())
    if cat_choices:
        cat_var.set(cat_choices[0])
    
    # 彻底放弃 ttk.Combobox，改用原生 OptionMenu 以解决显示问题
    cat_menu = tk.OptionMenu(input_frame, cat_var, *cat_choices)
    cat_menu.config(width=10)
    cat_menu.pack(side="left", padx=5)
    
    # 2. 品种
    tk.Label(input_frame, text="品种:").pack(side="left", padx=5)
    item_var = tk.StringVar()
    
    # 这里稍微复杂点，因为OptionMenu需要动态更新菜单项
    # 我们先创建一个空的OptionMenu，然后通过 trace 变量来更新它
    item_menu = tk.OptionMenu(input_frame, item_var, "")
    item_menu.config(width=15)
    item_menu.pack(side="left", padx=5)
    
    # 更新品种菜单的回调函数
    def update_item_options(*args):
        cat = cat_var.get()
        if cat in preset_categories:
            items = list(preset_categories[cat].keys())
            
            # 清除旧菜单
            menu = item_menu["menu"]
            menu.delete(0, "end")
            
            # 添加新菜单项
            for item in items:
                menu.add_command(label=item, command=lambda value=item: item_var.set(value))
                
            if items:
                item_var.set(items[0])
            else:
                item_var.set("")

    # 监听 cat_var 变化
    cat_var.trace("w", update_item_options)
    update_item_options() # 初始化
    
    # 3. 添加按钮
    tk.Button(input_frame, text="添加", command=on_preset_add, width=8).pack(side="left", padx=10)

    # === 搜索区域 ===
    search_frame = tk.LabelFrame(settings_win, text="搜索股票 (输入名称/代码)", padx=5, pady=5)
    search_frame.pack(fill="x", padx=5, pady=5)
    
    # 顶部输入行
    input_frame = tk.Frame(search_frame)
    input_frame.pack(fill="x", side="top")

    search_var = tk.StringVar()
    search_entry = tk.Entry(input_frame, textvariable=search_var)
    search_entry.pack(side="left", fill="x", expand=True, padx=5)
    
    def do_search():
        keyword = search_var.get().strip()
        if not keyword: return
        
        # 清空搜索结果
        search_listbox.delete(0, tk.END)
        
        results = search_stocks_sina(keyword)
        if not results:
            messagebox.showinfo("提示", "未找到相关股票")
            return
            
        for code, name in results:
            search_listbox.insert(tk.END, f"{code} - {name}")
            
    tk.Button(input_frame, text="搜索", command=do_search).pack(side="left", padx=5)
    
    # 搜索结果列表 (下部)
    search_listbox = tk.Listbox(search_frame, height=6)
    search_listbox.pack(fill="x", side="top", padx=5, pady=5)
    
    def on_search_select(event):
        selection = search_listbox.curselection()
        if selection:
            item = search_listbox.get(selection[0])
            # item: "sh518880 - 黄金ETF"
            code, name = item.split(' - ', 1)
            code_entry.delete(0, tk.END)
            code_entry.insert(0, code)
            name_entry.delete(0, tk.END)
            name_entry.insert(0, name)
            
    search_listbox.bind('<<ListboxSelect>>', on_search_select)

    # === 编辑区域 ===
    edit_frame = tk.LabelFrame(settings_win, text="编辑/添加", padx=5, pady=5)
    edit_frame.pack(fill="x", padx=5, pady=5)
    
    tk.Label(edit_frame, text="代码:").grid(row=0, column=0, padx=5)
    code_entry = tk.Entry(edit_frame)
    code_entry.grid(row=0, column=1, padx=5)
    
    tk.Label(edit_frame, text="名称:").grid(row=0, column=2, padx=5)
    name_entry = tk.Entry(edit_frame)
    name_entry.grid(row=0, column=3, padx=5)
    
    # === 联系作者 (Bottom) ===
    contact_frame = tk.Frame(settings_win)
    contact_frame.pack(side="bottom", fill="x", pady=10)
    
    contact_right = tk.Frame(contact_frame)
    contact_right.pack(side="right", padx=20)
    
    tk.Label(contact_right, text="有问题联系我 👉", font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 5))
    
    def show_qrcode():
        try:
            qr_path = "qrcode_for_gh_d40602192370_344.jpg"
            # 尝试绝对路径
            if not os.path.exists(qr_path):
                current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
                qr_path = os.path.join(current_dir, "qrcode_for_gh_d40602192370_344.jpg")
            
            if not os.path.exists(qr_path):
                # 再试一下 D:\doc\stock_monitor_project\qrcode_for_gh_d40602192370_344.jpg
                qr_path = r"D:\doc\stock_monitor_project\qrcode_for_gh_d40602192370_344.jpg"
                
            if not os.path.exists(qr_path):
                messagebox.showerror("错误", f"找不到二维码文件")
                return
                
            top = tk.Toplevel(settings_win)
            top.title("扫码关注公众号")
            top.geometry("400x400")
            
            img = Image.open(qr_path)
            img.thumbnail((350, 350))
            photo = ImageTk.PhotoImage(img)
            
            lbl = tk.Label(top, image=photo)
            lbl.image = photo 
            lbl.pack(expand=True, fill="both")
            
        except Exception as e:
            messagebox.showerror("错误", f"无法打开图片: {e}")

    tk.Button(contact_right, text="关注公众号", command=show_qrcode, bg="#4CAF50", fg="white").pack(side="left")

    # === 列表区域 ===
    list_frame = tk.LabelFrame(settings_win, text="当前监控列表", padx=5, pady=5)
    list_frame.pack(fill="both", expand=True, padx=5, pady=5)
    
    stock_listbox = tk.Listbox(list_frame)
    stock_listbox.pack(side="left", fill="both", expand=True)
    
    scrollbar = tk.Scrollbar(list_frame)
    scrollbar.pack(side="right", fill="y")
    stock_listbox.config(yscrollcommand=scrollbar.set)
    scrollbar.config(command=stock_listbox.yview)

    def refresh_list():
        stock_listbox.delete(0, tk.END)
        for stock in STOCKS:
            stock_listbox.insert(tk.END, f"{stock['code']} - {stock['name']}")
            
    refresh_list()
    
    def on_stock_select(event):
        selection = stock_listbox.curselection()
        if selection:
            idx = selection[0]
            stock = STOCKS[idx]
            code_entry.delete(0, tk.END)
            code_entry.insert(0, stock['code'])
            name_entry.delete(0, tk.END)
            name_entry.insert(0, stock['name'])
            
    stock_listbox.bind('<<ListboxSelect>>', on_stock_select)
    
    # === 按钮操作 ===
    def add_or_update():
        code = code_entry.get().strip()
        name = name_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "代码不能为空")
            return
            
        # 检查是否已存在（更新）
        selection = stock_listbox.curselection()
        if selection:
            # 更新模式
            idx = selection[0]
            STOCKS[idx] = {"code": code, "name": name}
        else:
            # 添加模式 (或者如果不选中，也检查是否有重复代码？简单起见，默认添加)
            # 也可以遍历检查重复
            found = False
            for i, s in enumerate(STOCKS):
                if s['code'] == code:
                    STOCKS[i] = {"code": code, "name": name}
                    found = True
                    break
            if not found:
                STOCKS.append({"code": code, "name": name})
        
        save_config()
        refresh_list()
        # 清空输入
        code_entry.delete(0, tk.END)
        name_entry.delete(0, tk.END)
        
        # 立即刷新UI
        if root: root.after(0, lambda: refresh_labels({}))
        
    def delete_stock():
        selection = stock_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的股票")
            return
        
        idx = selection[0]
        del STOCKS[idx]
        save_config()
        refresh_list()
        code_entry.delete(0, tk.END)
        name_entry.delete(0, tk.END)
        
        # 立即刷新UI
        if root: root.after(0, lambda: refresh_labels({}))

    btn_frame = tk.Frame(edit_frame)
    btn_frame.grid(row=1, column=0, columnspan=4, pady=10)
    
    tk.Button(btn_frame, text="保存/更新 (Save)", command=add_or_update, bg="#dddddd").pack(side="left", padx=10)
    tk.Button(btn_frame, text="删除选中 (Delete)", command=delete_stock, fg="red").pack(side="left", padx=10)


def minimize_window(event=None):
    """最小化窗口"""
    # 先取消 overrideredirect，否则无法在任务栏显示图标
    root.overrideredirect(False)
    root.iconify()

def on_map(event):
    """窗口恢复时的处理"""
    # 只有当窗口是有边框状态(overrideredirect=False)且状态为normal时才恢复无边框
    # 这样避免了已经是无边框状态时的重复触发
    # 注意：root.overrideredirect() 返回的是布尔值或整数
    if root.state() == 'normal' and not root.overrideredirect():
        root.after(100, lambda: root.overrideredirect(True))

def main():
    global root
    
    # === 关键修改：开启高DPI感知，解决字体模糊问题 ===
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
    # ===============================================

    load_config()

    root = tk.Tk()
    root.title("") # 无标题
    
    # === 窗口属性设置 ===
    root.overrideredirect(True)       # 无边框
    root.wm_attributes("-topmost", True) # 置顶
    root.wm_attributes("-alpha", 0.6)    # 透明度
    root.configure(bg="black")           # 背景色
    
    # 初始位置和大小
    root.geometry(f"220x{len(STOCKS)*40}+100+100") 
    
    # 退出事件：双击最小化
    root.bind("<Double-Button-1>", minimize_window)
    # 监听窗口恢复事件
    root.bind("<Map>", on_map)
    
    # 拖拽事件
    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", on_drag)
    # 右键菜单
    root.bind("<Button-3>", show_context_menu)
    
    # 初始化Labels (首次)
    refresh_labels({})
        
    # 启动数据更新线程
    t = threading.Thread(target=update_ui_loop, daemon=True)
    t.start()
    
    # 启动 MA5 获取线程
    ma5_thread = threading.Thread(target=get_ma5_volumes_thread, daemon=True)
    ma5_thread.start()
    
    root.mainloop()

if __name__ == "__main__":
    main()
