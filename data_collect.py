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

def ceil(n, r):
    """
    same as floor, but rounds up
    """
    if r <= 0:
        return str(int(n) + (float(n) > int(n)))
    s = str(float(n))
    if 'e' in s:
        s1 = s.split('e-')
        s2 = ''
        for i in range(int(s1[1]) - 1):
            if i == '.':
                continue
            s2 = s2 + '0'
        s3 = ''
        for i in s1[0]:
            if i == '.':
                continue
            s3 = s3 + i
        s = '0.' + s2 + s3
        if len(s) > r + 2:
            return s[:r+1] + str(int(s[r+1])+1)
        else:
            return s[:r+2]
    else:
        s = s.split('.')
        x = s[1]
        if len(s[1]) > r:
            x = s[1][:r-1] + str(int(x[r-1])+1)
        return s[0] + '.' + x[:r]

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
            self.quantity_round[symbol] = stepSize.index('1') - 1
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
        """ cnt = 0
        while True:
            if cnt>=4:
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
                    pivot_to_btc = self.buy_ethbtc_market()
                    cnt+=1 """
        while True:   
            pivot_stat = self.test_pivot_time()
            if pivot_stat != False:
                self.occupied_alts[str(pivot_stat)] = 0
            time.sleep(self.wait_time) 

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
                    """ if ((bqty_2>self.min_notional[t2])&(aqty_1>self.min_notional[t1])&((aqty_1*ask)>self.min_notional[t2])):
                        ev = bid_2/(ask_1*ask)
                    else:
                        ev = 0 """
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
        print("program start!")
        start_time = time.time()
        pivot = False
        #cnt=0
        while not pivot:
            """ if cnt>=10:
                print("stoped!!")
                sys.exit(0)
            cnt+=1 """
            get_pivot_return = self.get_pivot()
            if get_pivot_return != False :
                pivot = str(get_pivot_return)
                s1, s2 = pivot + self.c1, pivot + self.c2
                t1, t2 = self.orderbook_tickers_dict[s1], self.orderbook_tickers_dict[s2]
                bid_1, bid_2 =float(t1['b']), float(t2['b'])
                bid_q1, bid_q2 = float(t1['B']), float(t2['B'])      
                ask_1, ask_2 =float(t1["a"]), float(t2["a"])
                ask_q1, ask_q2 =float(t1["A"]), float(t2["A"])
                try:
                    bid =float(self.orderbook_tickers_dict[self.c1+self.c2]['b'])       
                    bid_q =float(self.orderbook_tickers_dict[self.c1+self.c2]['B'])    
                    ask = float(self.orderbook_tickers_dict[self.c1+self.c2]['a'])
                    ask_q = float(self.orderbook_tickers_dict[self.c1+self.c2]['A'])
                except:
                    print("get ETHBTC price failed")
                    return False 
                e_roi =  bid_2/(ask_1*ask)
        once_time_cost = time.time()-start_time
        """ if once_time_cost>0.005:
            time.sleep(0.3)
            return False """
        print("get pivot!!!!!!!! ",pivot," E(roi)=",e_roi," time cost=",once_time_cost)
        
        time.sleep(0.03)
        s1, s2 = pivot + self.c1, pivot + self.c2
        t1, t2 = self.orderbook_tickers_dict[s1], self.orderbook_tickers_dict[s2]
        bid_1_30, bid_2_30 =float(t1['b']), float(t2['b'])
        bid_q1_30, bid_q2_30 = float(t1['B']), float(t2['B'])      
        ask_1_30, ask_2_30 =float(t1["a"]), float(t2["a"])
        ask_q1_30, ask_q2_30 =float(t1["A"]), float(t2["A"])
        try:
            bid_30 =float(self.orderbook_tickers_dict[self.c1+self.c2]['b'])       
            bid_q_30 =float(self.orderbook_tickers_dict[self.c1+self.c2]['B'])    
            ask_30 = float(self.orderbook_tickers_dict[self.c1+self.c2]['a'])
            ask_q_30 = float(self.orderbook_tickers_dict[self.c1+self.c2]['A'])
        except:
            print("get ETHBTC price failed")
            return False 
        roi_30 =  bid_2_30/(ask_1_30 *ask_30)
        print("roi after 30ms is ", roi_30)

        time.sleep(0.07)
        s1, s2 = pivot + self.c1, pivot + self.c2
        t1, t2 = self.orderbook_tickers_dict[s1], self.orderbook_tickers_dict[s2]
        bid_1_100, bid_2_100 =float(t1['b']), float(t2['b'])
        bid_q1_100, bid_q2_100 = float(t1['B']), float(t2['B'])      
        ask_1_100, ask_2_100 =float(t1["a"]), float(t2["a"])
        ask_q1_100, ask_q2_100 =float(t1["A"]), float(t2["A"])
        try:
            bid_100 =float(self.orderbook_tickers_dict[self.c1+self.c2]['b'])       
            bid_q_100 =float(self.orderbook_tickers_dict[self.c1+self.c2]['B'])    
            ask_100 = float(self.orderbook_tickers_dict[self.c1+self.c2]['a'])
            ask_q_100 = float(self.orderbook_tickers_dict[self.c1+self.c2]['A'])
        except:
            print("get ETHBTC price failed")
            return False 
        roi_100 =  bid_2_100/(ask_1*ask_100)
        print("roi after 100ms is ", roi_100)

        time.sleep(0.2)
        s1, s2 = pivot + self.c1, pivot + self.c2
        t1, t2 = self.orderbook_tickers_dict[s1], self.orderbook_tickers_dict[s2]
        bid_1_300, bid_2_300 =float(t1['b']), float(t2['b'])
        bid_q1_300, bid_q2_300 = float(t1['B']), float(t2['B'])      
        ask_1_300, ask_2_300 =float(t1["a"]), float(t2["a"])
        ask_q1_300, ask_q2_300 =float(t1["A"]), float(t2["A"])
        try:
            bid_300 =float(self.orderbook_tickers_dict[self.c1+self.c2]['b'])       
            bid_q_300 =float(self.orderbook_tickers_dict[self.c1+self.c2]['B'])    
            ask_300 = float(self.orderbook_tickers_dict[self.c1+self.c2]['a'])
            ask_q_300 = float(self.orderbook_tickers_dict[self.c1+self.c2]['A'])
        except:
            print("get ETHBTC price failed")
            return False 
        roi_300 =  bid_2_300/(ask_1_300*ask_300)
        print("roi after 300ms is ", roi_300)

        z = time.strftime("%Y%m%d %H:%M:%S", time.localtime())
        day = z[0:8]
        filename = "arbitrage_data_"+str(self.c1+self.c2)+str(day)+".csv"
        try:
            df = pd.read_csv(filename)
        except:
            df=pd.DataFrame(columns=['start_time','crypto_code','e_roi','time_cost',
                "ask",'ask_q','ask_1',"ask_1_q",'bid_2','bid_2_q',
                'roi_30',"ask_30",'ask_q_30','ask_1_30',"ask_1_q_30",'bid_2_30','bid_2_q_30',
                "roi_100","ask_100",'ask_q_100','ask_1_100',"ask_1_q_100",'bid_2_100','bid_2_q_100',
                'roi_300',"ask_300",'ask_q_300','ask_1_300',"ask_1_q_300",'bid_2_300','bid_2_q_300']) 
        #start_time,crypto_code,e_roi,time_cost,roi_30,roi_100,roi_300,roi_1000
        line = pd.Series({
            'start_time':time.time(), 'crypto_code':pivot, 
            'e_roi':e_roi,'time_cost':once_time_cost,
            "ask":ask,'ask_q':ask_q,'ask_1':ask_1,"ask_1_q":ask_q1,'bid_2':bid_2,'bid_2_q':bid_q2,
            'roi_30':roi_30,"ask_30":ask_30,'ask_q_30':ask_q_30,
            'ask_1_30':ask_1_30,"ask_1_q_30":ask_q1_30,'bid_2_30':bid_2_30,'bid_2_q_30':bid_q2_30,
            "roi_100":roi_100,"ask_100":ask_100,'ask_q_100':ask_q_100,'ask_1_100':ask_1_100,
            "ask_1_q_100":ask_q1_100,'bid_2_100':bid_2_100,'bid_2_q_100':bid_q2_100,
            'roi_300':roi_300,"ask_300":ask_300,'ask_q_300':ask_q_300,'ask_1_300':ask_1_300,
            "ask_1_q_300":ask_q1_300,'bid_2_300':bid_2_300,'bid_2_q_300':bid_q2_300
            })
        df = df.append(line, ignore_index=True)
        df.to_csv(filename,index=False)
        return pivot

    def buy_pivot(self):
        start_time = time.time()
        pivot = False
        while not pivot:
            get_pivot_return = self.get_pivot()
            if get_pivot_return != False :
                pivot = str(get_pivot_return)
        
        once_time_cost = time.time()-start_time
        if once_time_cost>0.005:
            time.sleep(0.3)
            return False
        
        print("get pivot!!!!!!!!",pivot)
        symbol = str(pivot) + self.c1
        try:
            bid_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c2]['B'])
            ask_qty = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['A'])
            ask_price = float(self.orderbook_tickers_dict[str(pivot) + self.c1]['a'])
        except:
            print("get pivot order qty failed!")
            return False    #can't afford time cost
        qty = min(ask_qty,bid_qty)

        eth_f = float(self.asset_balances[self.c1]['f'])
        starting_amount = min(self.starting_amount, eth_f)
        qty = min(qty, starting_amount/ask_price)
        print(qty)
        qty = float(floor(qty, self.quantity_round[symbol]))
        print("2nd",qty)
        print("------------begin trade--------------")
        x = self.market_buy_pivot(symbol,qty,ask_price)
        if x == False:
            self.occupied_alts[pivot] = 0
            return False
        print("successfully_buy_pivot")
        return pivot

    def market_buy_pivot(self,symbol,qty,price):
        symbol = symbol.upper()

        if self.quantity_errors_buy(qty,symbol,price):
            try:
                order_info = self.client.order_market_buy(symbol=symbol, quoteOrderQty=qty)
                self.order_info_dict[symbol] = order_info
                return order_info['orderId']
            except:
                print("ERROR:-------by pivot error!")
                return False
        else:
            return False

    def sell_at_market(self,pivot):
        pivot = str(pivot)
        symbol = pivot + self.c2
        max_sell = float(self.asset_balances[pivot]['f'])
        # provement: judge 2 direction, for stop loss
        if max_sell == 0:
            print("error when sell pivot to BTC, max_sell=0")
            return True
        qty = float(floor(max_sell, self.quantity_round[symbol]))

        try:
            self.order_info_dict[symbol] = self.client.order_market_sell(symbol=symbol, quantity=qty)
            self.occupied_alts[str(pivot)] = 0
            return True
        except:
            print("error when sell pivot to BTC")
            return False

    def buy_ethbtc_market(self):
        symbol = self.c1 + self.c2
        qty = float(self.asset_balances[self.c2]["f"])
        #print(qty)
        self.order_info_dict[symbol] = self.client.order_market_buy(symbol=symbol, quoteOrderQty=qty)
        return True

    def quantity_errors_buy(self, qty, symbol, bid):
        if qty < float(self.min_quantity[symbol]):
            print('buy quantity too low')
            return False
        elif qty > float(self.max_quantity[symbol]):
            print('buy quantity too high')
            return False
        if qty*float(bid) < float(self.min_notional[symbol]):
            print('purchase too small')
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
        z = (self.client.get_server_time()['serverTime']-int(time.time()*1000))
        return z

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
                bab.asset_balances[i['asset']] = i
            if not bab.buy_eth_lock.locked():
                print("c1 not enough, please buy more")
                #threading.Thread(target=bab.buy_eth).start()
        except:
            print(i)

if __name__ == "__main__":
    print(time.time())
    #parameter
    c1 = "BNB"
    c2 = "USDT"
    thread_num = 1              # number of trades to make simultaneously
    starting_amount = .052      # max quantity of ETH to be used per trade
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

    bm = BinanceSocketManager(client)
    bm.start_book_ticker_socket(update_orderbook_dict)
    bm.start_user_socket(update_user)
    bm.start()
    time.sleep(10) # wait for websocket response
    
    #bab.check_balance(amount=(thread_num*starting_amount), symbol=bab.c1)

    while not 'QTUM' in bab.asset_balances: # wait for websocket response to get all asset balances, QTUM is normally one of last assets to get
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











