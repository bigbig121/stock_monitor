import requests
import datetime

def get_kline_data(code):
    """获取K线数据 (腾讯接口)"""
    # 处理代码前缀
    api_code = code
    if code.startswith("sh00") or code.startswith("sh60") or code.startswith("sh68"):
        pass # shXXXXXX
    elif code.startswith("sz"):
        pass
    else:
        # 简单处理，默认为sh
        pass
        
    # 获取100天日K
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,100,qfq"
    try:
        resp = requests.get(url, timeout=2)
        data = resp.json()
        
        # 解析数据
        # 路径: data['data'][code]['day'] or 'qfqday'
        stock_data = data['data'].get(code, {})
        kline = stock_data.get('qfqday', stock_data.get('day', []))
        
        # 格式化: [date, open, close, high, low, volume]
        parsed_data = []
        for item in kline:
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
        print(f"Error: {e}")
        return []

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

def analyze_stock(code, name):
    print(f"\n======== {name} ({code}) 技术面AI分析 ========")
    data = get_kline_data(code)
    if not data:
        print("数据获取失败")
        return

    current_price = data[-1]['close']
    yesterday_price = data[-2]['close']
    
    # 1. 均线分析 (趋势)
    ma5 = calculate_ma(data, 5)
    ma20 = calculate_ma(data, 20)
    ma60 = calculate_ma(data, 60)
    
    print(f"当前价格: {current_price}")
    
    print("--- 趋势分析 ---")
    if current_price > ma20:
        print(f"✅ [多头] 股价位于20日均线({ma20:.2f})上方，中期趋势向好")
    else:
        print(f"⚠️ [空头] 股价位于20日均线({ma20:.2f})下方，中期趋势承压")
        
    if ma5 > ma20:
        print(f"✅ [攻击] 5日线 > 20日线，短期攻击形态")
    else:
        print(f"❄️ [调整] 5日线 < 20日线，短期处于调整/下跌中")

    # 2. 成交量分析 (资金)
    vol_today = data[-1]['volume']
    vol_ma5 = sum(d['volume'] for d in data[-6:-1]) / 5 # 昨天及之前的5天均量
    vol_ratio = vol_today / vol_ma5
    
    print("--- 资金分析 ---")
    if vol_ratio > 1.5:
        print(f"🔥 [放量] 今日量比 {vol_ratio:.2f}，资金介入明显")
    elif vol_ratio < 0.6:
        print(f"❄️ [缩量] 今日量比 {vol_ratio:.2f}，场内惜售，抛压减轻")
    else:
        print(f"📊 [平量] 今日量比 {vol_ratio:.2f}，交投情绪稳定")

    # 3. RSI分析 (超买超卖)
    rsi = calculate_rsi(data)
    print("--- 情绪分析 (RSI) ---")
    if rsi:
        print(f"RSI(14): {rsi:.2f}")
        if rsi > 80:
            print("⚠️ [超买] 情绪过热，随时可能回调")
        elif rsi < 20:
            print("💎 [超卖] 情绪冰点，反弹概率大")
        else:
            print("👉 [中性] 情绪处于正常波动区间")
            
    # 4. 综合建议
    print("--- 🤖 AI 综合研判 ---")
    score = 0
    if current_price > ma20: score += 1
    if vol_ratio > 1.5 and current_price > yesterday_price: score += 1 # 放量涨
    if vol_ratio < 0.6 and current_price > yesterday_price: score += 0.5 # 缩量涨(惜售)
    if rsi and rsi < 20: score += 1 # 超卖反弹机会
    
    if score >= 2:
        print("💡 结论：建议【持有/买入】。技术指标偏强。")
    elif score <= 0:
        print("🛑 结论：建议【观望/减仓】。技术指标偏弱。")
    else:
        print("👀 结论：建议【观察】。多空分歧，等待方向明确。")

if __name__ == "__main__":
    # 分析你的持仓
    analyze_stock("sh588000", "科创50ETF")
    analyze_stock("sh000832", "中证转债")
