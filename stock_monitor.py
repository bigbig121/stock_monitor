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

# 刷新频率（秒）
REFRESH_RATE = 3

# 字体设置 
# 使用 Microsoft YaHei UI 在 Windows 上显示更清晰
# 稍微加大字号以配合高DPI模式
FONT_CONFIG = ("Microsoft YaHei UI", 10, "bold") 
# ===========================================

def load_config():
    """加载配置文件"""
    global STOCKS
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                STOCKS = json.load(f)
        except Exception:
            STOCKS = DEFAULT_STOCKS
    else:
        STOCKS = DEFAULT_STOCKS

def save_config():
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(STOCKS, f, ensure_ascii=False, indent=4)
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

def refresh_labels(data_map):
    """在主线程刷新Labels"""
    global labels, root, last_percentages
    
    # 如果STOCKS变了，重新生成labels
    if len(labels) != len(STOCKS):
        for l in labels:
            l.destroy()
        labels = []
        
        for i, stock in enumerate(STOCKS):
            l = tk.Label(root, text="Loading...", bg="black", fg="white", font=FONT_CONFIG, anchor="center")
            l.pack(fill="x", pady=2)
            # 绑定事件
            l.bind("<Button-1>", start_drag)
            l.bind("<B1-Motion>", on_drag)
            l.bind("<Button-3>", show_context_menu) # 右键菜单
            l.bind("<Double-Button-1>", minimize_window)
            labels.append(l)
            
    # 更新内容
    max_text_len = 0
    should_shake = False
    
    for i, stock in enumerate(STOCKS):
        if i >= len(labels): break
        
        code = stock["code"]
        if code in data_map:
            price, percent = data_map[code]
            
            # === 检查是否需要抖动 ===
            if code in last_percentages:
                prev_percent = last_percentages[code]
                
                # 1. 盈亏转换 (跨越0轴)
                # 优化：包含0的情况，比如从0变成负数也算转亏
                if (prev_percent >= 0 and percent < 0) or (prev_percent <= 0 and percent > 0):
                    should_shake = True
                    print(f"Shake triggered: {stock['name']} {prev_percent}% -> {percent}%")
                    
                # 2. 整数关口突破 (如 0.9% -> 1.1%, 1.9% -> 2.1%)
                # 使用 int(abs()) 获取整数部分
                if int(abs(percent)) > int(abs(prev_percent)):
                    should_shake = True
            
            # 更新历史记录
            last_percentages[code] = percent
            # ========================
            
            # 颜色逻辑：涨红跌绿
            color = "#ff3333" if percent > 0 else "#00cc00"
            if percent == 0: color = "#cccccc"
            
            # 格式：名称 +0.50% 1776.27
            # 限制名称长度，防止过长
            display_name = stock['name']
            if len(display_name) > 8: # 稍微放宽一点
                display_name = display_name[:8]
                
            text = f"{display_name}  {percent:+.2f}%  {price:.2f}"
        else:
            text = f"{stock['name']} --"
            color = "#cccccc"
        
        labels[i].config(text=text, fg=color)
        
        # 计算大致宽度（非常粗略的估算）
        # 中文算2个字符宽度，英文数字算1个
        # 额外加一些padding
        current_len = 0
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                current_len += 22 # 稍微调大一点字号对应的像素
            else:
                current_len += 12
        if current_len > max_text_len:
            max_text_len = current_len

    # 动态调整窗口宽度
    target_height = len(STOCKS) * 40
    if target_height == 0: target_height = 40
    
    current_width = root.winfo_width()
    current_height = root.winfo_height()
    
    new_width = current_width
    if max_text_len > 0:
        # 加上左右padding
        calc_width = max_text_len + 40 
        # 保持最小宽度
        if calc_width < 220: calc_width = 220
        new_width = calc_width
        
    # 只在宽度变化较大或高度不一致时才调整
    width_diff = abs(new_width - current_width)
    height_diff = abs(target_height - current_height)
    
    if width_diff > 20 or height_diff > 0:
        final_width = int(new_width) if width_diff > 20 else current_width
        root.geometry(f"{final_width}x{target_height}+{root.winfo_x()}+{root.winfo_y()}")

    # 触发抖动
    if should_shake:
        # 使用 after 避免阻塞当前UI更新循环，虽然在主线程但也稍微延后一点
        root.after(50, shake_window)

def start_drag(event):
    event.widget.master.x = event.x
    event.widget.master.y = event.y

def on_drag(event):
    deltax = event.x - event.widget.master.x
    deltay = event.y - event.widget.master.y
    x = event.widget.master.winfo_x() + deltax
    y = event.widget.master.winfo_y() + deltay
    event.widget.master.geometry(f"+{x}+{y}")

def show_context_menu(event):
    """显示右键菜单"""
    menu = tk.Menu(root, tearoff=0)
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
