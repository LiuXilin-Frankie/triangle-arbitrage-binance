__author__ = 'AbsoluteX'
__email__ = 'l276660317@gmail.com'

import time
import math
import threading
from binance.client import Client
from binance.websockets import BinanceSocketManager

from email.mime.multipart import MIMEMultipart    
from email.mime.text import MIMEText    
from email.mime.image import MIMEImage
from email.header import Header
import smtplib
import json
import sys
import pandas as pd
import numpy as np

#proxy
#sudo ssserver -p 443 -k password -m rc4-md5 --user nobody -d start


def floor(n, r):
    """
    n is float, r is int, returns string
    rounds n down to r decimal places, and converts result to string while avoiding scientific notation
    used for the price param in placing orders
    example:
    >>> floor(.000000065423, 11)
    '0.00000006542'
    """
    if r <= 0:
        return str(int(n))
    s = str(float(n))
    if 'e' in s:
        s1 = s.split('e-')
        s2 = ''
        for i in range(int(s1[1]) - 1):
            if i == '.':
                continue
            s2 = s2 + '0'
        s3 = ''
        for i in  s1[0]:
            if i == '.':
                continue
            s3 = s3 + i
        s = '0.' + s2 + s3
        return s[:r+2]
    else:
        s = s.split('.')
        return s[0] + '.' + s[1][:r]

def floatPrecision(f, n):
    '''
    >>> floatPrecision(.234, 0.01000)
    '0.23'
    '''
    n = int(math.log10(1 / float(n)))
    f = math.floor(float(f) * 10 ** n) /(10 ** n)
    f = "{:0.0{}f}".format(float(f), n)
    return str(int(f)) if int(n) == 0 else f

class BinanceArbBot:

    def __init__(self, client, starting_amount, expected_roi, wait_time,c1,c2):
        self.client = client
        self.starting_amount = starting_amount
        self.min_ev = expected_roi + 1
        self.wait_time = wait_time
        self.c1 = c1
        self.c2 = c2
        self.btc_min_balance = 0
        #Emailing
        self.email_txt = "Find the pivot! Begin trading!"

        #put exchange info into dict where values are symbols, for easy access and placing orders without error
        
        info = self.client.get_exchange_info()
        self.quantity_round={}
        self.min_quantity={}
        self.max_quantity={}
        self.min_notional={} 
        self.tick_size={}
        self.price_round={}

        for s in info['symbols']:        
            symbol = s['symbol']
            try:
                stepSize = s['filters'][2]['stepSize']
            except:
                #sometimes the return msg doesn't fit the format                                #--------why this exist?
                stepSize = s['filters'][4]['stepSize']
            #self.quantity_round[symbol] = stepSize.index('1') - 1
            self.quantity_round[symbol] = stepSize
            self.min_quantity[symbol] = s['filters'][2]['minQty']
            self.max_quantity[symbol] = s['filters'][2]['maxQty']
            self.min_notional[symbol] = s['filters'][3]['minNotional']
            self.tick_size[symbol] = float(s['filters'][0]['tickSize'])
            self.price_round[symbol] = s['filters'][0]['tickSize'].index('1') - 1

        symbols = self.price_round.keys()
        self.symbol_full_list = list(symbols)
        self.price_round_float = {}

        for i in self.price_round.keys():
            self.price_round_float[i] = 1/math.pow(10, self.price_round[i])
        self.alts = []
        
        # self.alts = alts (base currencies) common to both ETH and BTC markets
        
        for s in symbols:
            if s.endswith(self.c1):
                if s[:-3] + self.c2 in symbols:
                    self.alts.append(s[:-3])

        #-------need remove all pivots
        self.occupied_alts = {}
        
        for alt in self.alts: 
            self.occupied_alts[alt] = 0

        self.orderbook_tickers_dict, self.order_info_dict, self.trade_status_dict  = {}, {}, {}
        
        for alt in self.alts: # initialized self.trade_status_dict
            self.trade_status_dict[alt+self.c1] = {'s':alt+self.c1, 'x':'NEW', 'q':'0', 'X':'NEW'}
            self.trade_status_dict[alt+self.c2] = {'s':alt+self.c2, 'x':'NEW', 'q':'0', 'X':'NEW'}
        self.trade_status_dict[self.c1+self.c2] = {'s':self.c1+self.c2, 'x':'NEW', 'q':'0', 'X':'NEW', 'T':0}
        self.trade_status_dict[self.c2+self.c1] = {'s':self.c2+self.c1, 'x':'NEW', 'q':'0', 'X':'NEW', 'T':0}
        
        self.sell_price_dict = {}
        self.asset_balances = {}

        z = self.client.get_account()
        for i in z['balances']:
            temp_balance = {}
            temp_balance['a'] = i['asset']
            temp_balance['f'] = i['free']
            temp_balance['l'] = i['locked']
            self.asset_balances[i["asset"]] = temp_balance

        self.pivot_lock = threading.Lock()
        self.buy_eth_lock = threading.Lock()

        self.orderbook_tickers = self.client.get_orderbook_tickers()
        self.orderbook_tickers_dict = {}
    
        for i in self.orderbook_tickers: # initialize values of self.orderbook_tickers_dict
            temp_book = {} #for the uniform format
            temp_book['u'] = int(1838597807)
            temp_book['s'] = i['symbol']
            temp_book['b'] = i['bidPrice']
            temp_book['B'] = i['bidQty']
            temp_book['a'] = i['askPrice']
            temp_book['A'] = i['askQty']
            self.orderbook_tickers_dict[i['symbol']] = temp_book
 
    def check_balance(self, amount=0.015, symbol="ETH"):
        # check if main currency balance is enough to trade
        # if not enough, coed will be stopped
        z = self.asset_balances[symbol]
        print(z)
        free = float(z["f"])
        if free>=(amount*1.1):
            print("enough to trade")
            return True
        if free>amount:
            print("can trade but not enough balance")
            return True
        if free<=amount:
            print("not enough, stop program")
            sys.exit(0)
            return True
    
    def make_trades(self):
        # starts trade when get pivot
        cnt = 0
        while True:
            if cnt>=5:
                sys.exit(0)
        # make sure code will always work        
            pivot_stat = self.buy_pivot()
            if pivot_stat != False:
                pivot_to_btc = False
                while pivot_to_btc==False:
                    pivot_to_btc = self.sell_at_market(pivot_stat)
                self.occupied_alts[str(pivot_stat)] = 0
                btc_to_eth =False
                while btc_to_eth==False:
                    btc_to_eth = self.buy_ethbtc_market()
            cnt+=1
        """ while True:   
            pivot_stat = self.test_pivot_time()
            if pivot_stat != False:
                self.occupied_alts[str(pivot_stat)] = 0
            time.sleep(self.wait_time) """ 

    def get_pivot(self):    # return [pivot,best_ev,{'ETHBTC_bid':bid,'ETHBTC_ask':ask,'Alt_ETH_bid':bid_1,'Alt_BTC_bid':bid_2}]
        with self.pivot_lock:
            pivot, best_ev = False, 0
            #cnt = 0
            for alt in self.alts:
                #cnt+=1
                if self.occupied_alts[alt]:
                    continue
                try:
                    s1, s2 = alt + self.c1, alt + self.c2
                    t1, t2 = self.orderbook_tickers_dict[s1], self.orderbook_tickers_dict[s2]
                    bid_2, bqty_2 = float(t2['b']), float(t2["B"])
                    ask_1, aqty_1 = float(t1["a"]), float(t1["A"])
                    ask = float(self.orderbook_tickers_dict[self.c1+self.c2]['a'])        
                except:
                    # if can't get continue for the next alt
                    continue

                try:
                    ''' if ((bqty_2>self.min_notional[t2])&(aqty_1>self.min_notional[t1])&((aqty_1*ask)>self.min_notional[t2])):
                        ev = bid_2/(ask_1*ask)
                    else:
                        ev = 0 '''
                    ev = bid_2/(ask_1*ask)
                except:
                    continue

                if ev >= self.min_ev:
                    self.occupied_alts[pivot] = 1
                    pivot = alt
                    best_ev = alt
                    return pivot

            if best_ev==0:
                return False

    def test_pivot_time(self):
        pivot = False
        while not pivot:
            get_pivot_return = self.get_pivot()
            if get_pivot_return != False :
                pivot = str(get_pivot_return)

        
        print("get pivot!!!!!!!!",pivot)
        symbol = str(pivot) + self.c1
        try:
            bid_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c2]['B'])
            ask_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['A'])
            ask_price = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['a'])
        except:
            print("get pivot order qty failed!")
            return False    #can't afford time cost
        print("ask_qty,bid_qty ",ask_qty,bid_qty)

        #抓你所有需要的价格
        ## ie. abc  a; BNB   b:LTC    c:  USDT
        #trade  LTCBNB   LTCUSDT   BNBUSDT    arbitrage
        #在抓到交易信号的一瞬间 有三个价格，记录一下，甚至记录一下qty

    def buy_pivot(self):
        pivot = False
        while not pivot:
            get_pivot_return = self.get_pivot()
            if get_pivot_return != False :
                pivot = str(get_pivot_return)

        
        print("get pivot!!!!!!!!",pivot)
        symbol = str(pivot) + self.c1
        try:
            bid_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c2]['B'])
            ask_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['A'])
            ask_price = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['a'])
        except:
            print("get pivot order qty failed!")
            return False    #can't afford time cost
        print("ask_qty,bid_qty ",ask_qty,bid_qty)


        qty = min(ask_qty,bid_qty)

        eth_f = float(self.asset_balances[self.c1]['f'])
        starting_amount = min(self.starting_amount, eth_f)
        print('qty,s_a/ask_price ',qty,starting_amount/ask_price)
        print("ask price is ",ask_price)
        qty = min(qty, starting_amount/ask_price)
        
        qty = float(floatPrecision(qty, self.quantity_round[symbol]))
        print("2nd",qty," try decimals:",self.quantity_round[symbol])
        print("------------begin trade--------------")
        x = self.market_buy_pivot(symbol,qty,ask_price)
        if x == False:
            self.occupied_alts[pivot] = 0
            return False
        print("successfully_buy_pivot")
        return pivot

    def market_buy_pivot(self,symbol,qty,price):
        symbol = symbol.upper()
        if self.quantity_errors_buy(qty,symbol,price):      #used for catching the error
            print("symbol,qty: ",symbol,qty)
            start_time = time.time()
            order_info = self.client.order_market_buy(symbol=symbol, quantity=str(qty))
            print("time cost for place the buy pivot order is:",time.time()-start_time)
            self.order_info_dict[symbol] = order_info
            return order_info['orderId']
        else:
            time.sleep(0.1)
            return False

    def sell_at_market(self,pivot):
        pivot = str(pivot)
        symbol = pivot + self.c2
        max_sell = float(self.asset_balances[pivot]['f'])
        # provement: judge 2 direction, for stop loss
        if max_sell == 0:
            print("pivot is: ",pivot)
            print(self.asset_balances[pivot]['f'])
            print("error when sell pivot to BTC, max_sell=0")
            time.sleep(0.01)
            return False

        qty = float(floatPrecision(max_sell,self.quantity_round[symbol]))

        try:
            self.order_info_dict[symbol] = self.client.order_market_sell(symbol=symbol, quantity=str(qty))
            self.occupied_alts[str(pivot)] = 0
            return True
        except:
            print("error when sell pivot to BTC")
            return False    

    def buy_ethbtc_market(self):
        symbol = self.c1 + self.c2
        qty = float(self.asset_balances[self.c2]["f"])
        print("qty from c1 to c2 is:",qty)
        try:
            self.order_info_dict[symbol] = self.client.order_market_buy(symbol=symbol, quoteOrderQty=str(qty))
            return True
        except:
            print("fail to but c1, maybe wait for websocket")
            time.sleep(0.01)
            return False

    def quantity_errors_buy(self, qty, symbol, bid):
        if qty < float(self.min_quantity[symbol]):
            print('buy quantity too low')
            return False
        elif qty > float(self.max_quantity[symbol]):
            print('buy quantity too high')
            return False
        if qty*float(bid) < float(self.min_notional[symbol]):
            print("your notion is ",qty,float(bid),qty*float(bid))
            print('purchase too small, min_notional is ',self.min_notional[symbol])
            return False           #the meanings of min_notional
        return True

    def quantity_errors_sell(self, qty, symbol, price):
        if qty < float(self.min_quantity[symbol]):
            print('not enough to sell')
            return False
        if qty > float(self.max_quantity[symbol]):
            print("sell amount too high")
            return False
        if qty*price < float(self.min_notional[symbol]):
            print('sale too small')
            return False
        return True

    def get_time_diff(self):
        start_time = int(time.time()*1000)
        z = self.client.get_server_time()['serverTime']
        end_time = int(time.time()*1000)
        print('request time is ',(end_time-start_time))
        temp = int(time.time()*1000)
        z = (self.client.get_server_time()['serverTime']-temp -0.5*(end_time-start_time))
        return z


if __name__ == "__main__":
    print(time.time())
    #parameter
    c1 = "BNB"
    c2 = "USDT"
    thread_num = 1              # number of trades to make simultaneously
    starting_amount = .07       # max quantity of ETH to be used per trade
    expected_roi = .004         # the expected return you will get in one sequence trade, without fees and slip
    wait_time = 0.1             # time you need to confirm that BTC is traded to buy ETH
    
    api_key = "KFuwwOEa174YyUheOOpJZZEdYdxf1tSTr91wSedFQ0sz65huIPigTaiiWwparkCB"
    api_secret = "vGwdLLCN8IQa7TGLV4yBad2Wyel5nfKUjSoZrOqZVvK4m5GdtCADDtXeOGb5vtNI"

    print("getting ----",c1,c2)
    client = Client(api_key, api_secret, {'timeout':600})
    bab = BinanceArbBot(client, starting_amount=starting_amount, expected_roi=expected_roi, wait_time=wait_time,c1=c1,c2=c2)
    print('-'*10+"BinanceArbBot built"+'-'*10)
    cnt=0
    while cnt<10:
        time_diff = bab.get_time_diff()
        print("latency: ",time_diff)
        cnt+=1
    ''' if time_diff>=400:
        print('high latency')
        sys.exit(0) '''

    #define websocket update message function   
    def update_orderbook_dict(msg): # callback function for start_ticker_socket
        bab.orderbook_tickers_dict[msg['s']] = msg

    def update_user(msg): # callback function for start_user_socket
        if msg['e'] == 'executionReport':
            bab.trade_status_dict[msg['s']] = msg
        else:
            balances = msg['B']
            try:
                for i in balances:
                    bab.asset_balances[i['a']] = i
                    #if not bab.buy_eth_lock.locked():
                        #print("c1 not enough, please buy more")
                        #threading.Thread(target=bab.buy_eth).start()
            except:
                print(msg)

    bm = BinanceSocketManager(client)
    bm.start_book_ticker_socket(update_orderbook_dict)
    bm.start_user_socket(update_user)
    bm.start()
    time.sleep(10) # wait for websocket response
    
    bab.check_balance(amount=(thread_num*starting_amount), symbol=bab.c1)

    while not 'QTUM' in bab.asset_balances: 
        # wait for websocket response to get all asset balances, QTUM is normally one of last assets to get
        pass

    def start_trading(thread_num):
        for i in range(thread_num):
            threading.Thread(target=bab.make_trades).start()

    start_trading(thread_num) # start running bot
    print("____________start_getting_pivot_________")


'''
    for i in list(range(4)):
        print("-"*10+"Getting pivot "+ str(i) + '-'*10)
        bab.get_pivot_send_email()
        print('$'*10 + ' ' + str(i+1) + " time")
        time.sleep(2)
    #Close all connection
    print('Done!!!\n----------Closing program!---------------')
    bm.close()
'''











