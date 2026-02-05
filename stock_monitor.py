import tkinter as tk
from tkinter import simpledialog, messagebox
import requests
import time
import threading
import ctypes
import json
import os

import math
import random

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
session_max_map = {} # 本次运行期间每只股票出现过的最大涨跌幅绝对值 {code: max_percent}

# 刷新频率（秒）
REFRESH_RATE = 3

# 字体设置 
# 使用 Microsoft YaHei UI 在 Windows 上显示更清晰
# 稍微加大字号以配合高DPI模式
FONT_CONFIG = ("Microsoft YaHei UI", 10, "bold") 
# ===========================================

def load_config():
    """加载配置文件"""
    global STOCKS, display_mode
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
        except Exception:
            STOCKS = DEFAULT_STOCKS
    else:
        STOCKS = DEFAULT_STOCKS

def save_config():
    """保存配置文件"""
    try:
        data = {
            "stocks": STOCKS,
            "display_mode": display_mode
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_stock_data_tencent(codes):
    """
    从腾讯财经接口批量获取数据
    接口地址示例：http://qt.gtimg.cn/q=sh000681,sh000832
    """
    if not codes:
        return {}
    try:
        # 拼接代码，如 sh000681,sh000832
        code_str = ",".join([s["code"] for s in codes])
        url = f"http://qt.gtimg.cn/q={code_str}"
        resp = requests.get(url, timeout=2)
        
        # 腾讯接口返回GBK编码，需要正确解码
        content = resp.content.decode('gbk')
        
        results = {}
        # 解析返回数据
        # 格式：v_sh000681="1~科创价格~000681~1775.82~1767.44~..."
        lines = content.strip().split(';')
        for line in lines:
            if '="' not in line: continue
            
            # 提取代码和数据
            # line: v_sh000681="1~..."
            key = line.split('="')[0].split('_')[1] # sh000681
            data_str = line.split('="')[1].strip('"')
            data = data_str.split('~')
            
            if len(data) > 32:
                current_price = float(data[3])
                percent = float(data[32])
                results[key] = (current_price, percent)
                
        return results
            
    except Exception as e:
        # print(f"Error: {e}")
        return {}

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

def bind_events(widget):
    """绑定通用事件到组件"""
    widget.bind("<Button-1>", start_drag)
    widget.bind("<B1-Motion>", on_drag)
    widget.bind("<Button-3>", show_context_menu)
    widget.bind("<Double-Button-1>", minimize_window)

def refresh_labels(data_map):
    """在主线程刷新Labels (重构版：支持Grid布局)"""
    global main_frame, stock_row_widgets, last_display_mode, last_stock_count, root, last_percentages
    
    if not root: return
    
    # 初始化主容器
    if main_frame is None:
        main_frame = tk.Frame(root, bg="black")
        main_frame.pack(fill="both", expand=True)
        bind_events(main_frame) # 允许拖动背景
        
    # 检查是否需要重建布局
    # 条件：模式改变 或 股票数量改变 (这里简单判断数量，更严谨应该判断内容，但数量通常足够)
    need_rebuild = (display_mode != last_display_mode) or (len(STOCKS) != last_stock_count)
    
    if need_rebuild:
        # 清除旧组件
        for widget in main_frame.winfo_children():
            widget.destroy()
        stock_row_widgets = []
        
        # 重建布局
        for i, stock in enumerate(STOCKS):
            row_widgets = {}
            
            # 1. 名称 (所有模式都有)
            name_label = tk.Label(main_frame, text=stock['name'], bg="black", fg="white", 
                                 font=FONT_CONFIG, anchor="w")
            name_label.grid(row=i, column=0, sticky="nswe", padx=(10, 5), pady=2)
            bind_events(name_label)
            row_widgets['name'] = name_label
            
            if display_mode == "bar":
                # 2. 柱状图 (Canvas)
                # 增加宽度到 150px，提升显示精度
                bar_canvas = tk.Canvas(main_frame, bg="black", height=24, width=150, highlightthickness=0)
                bar_canvas.grid(row=i, column=1, sticky="nswe", padx=5, pady=2)
                bind_events(bar_canvas)
                row_widgets['bar'] = bar_canvas
                
                # 3. 百分比
                pct_label = tk.Label(main_frame, text="--%", bg="black", fg="white",
                                    font=("Microsoft YaHei UI", 10, "bold"), anchor="e")
                pct_label.grid(row=i, column=2, sticky="nswe", padx=(5, 10), pady=2)
                bind_events(pct_label)
                row_widgets['pct'] = pct_label
                
            else: # percent mode
                # 2. 百分比 (直接放在第二列)
                pct_label = tk.Label(main_frame, text="--%", bg="black", fg="white",
                                    font=FONT_CONFIG, anchor="e")
                pct_label.grid(row=i, column=1, sticky="nswe", padx=(20, 10), pady=2) # 增加左侧间距实现"双列对齐"
                bind_events(pct_label)
                row_widgets['pct'] = pct_label
                
            stock_row_widgets.append(row_widgets)
            
        last_display_mode = display_mode
        last_stock_count = len(STOCKS)
        
        # 配置列权重
        main_frame.grid_columnconfigure(0, weight=0) # 名称列自适应
        if display_mode == "bar":
            main_frame.grid_columnconfigure(1, weight=0) # 柱状图固定
            main_frame.grid_columnconfigure(2, weight=0) # 百分比自适应
        else:
            main_frame.grid_columnconfigure(1, weight=1) # 百分比列稍微弹一下？或者也自适应
            
    # === 更新数据 ===
    global session_max_map
    
    # 1. 更新每只股票的历史最大值 (Session Max)
    for code in data_map:
        _, percent = data_map[code]
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
        
        if code in data_map:
            _, percent = data_map[code]
            color = "#ff3333" if percent > 0 else "#00cc00"
            if percent == 0: color = "#cccccc"
            
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
        
        # 更新百分比
        pct_text = f"{percent:+.2f}%" if code in data_map else "--"
        widgets['pct'].config(text=pct_text, fg=color)
        
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

def show_context_menu(event):
    """显示右键菜单"""
    menu = tk.Menu(root, tearoff=0)
    
    # 显示模式子菜单
    mode_menu = tk.Menu(menu, tearoff=0)
    mode_menu.add_radiobutton(label="纯百分比 (Percent)", command=lambda: toggle_display_mode("percent"))
    mode_menu.add_radiobutton(label="柱状图 (Bar Chart)", command=lambda: toggle_display_mode("bar"))
    # 设置当前选中项 (Radiobutton需要variable才能同步显示选中状态，这里简化处理，只提供功能)
    
    menu.add_cascade(label="显示模式 (Display Mode)", menu=mode_menu)
    menu.add_separator()
    menu.add_command(label="设置 (Settings)", command=open_settings)
    menu.add_command(label="测试抖动 (Test Shake)", command=shake_window) # 方便测试
    menu.add_separator()
    menu.add_command(label="退出 (Exit)", command=root.destroy)
    menu.post(event.x_root, event.y_root)

def open_settings():
    """打开设置窗口"""
    settings_win = tk.Toplevel(root)
    settings_win.title("配置股票")
    settings_win.geometry("700x700")
    
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
    
    root.mainloop()

if __name__ == "__main__":
    main()
