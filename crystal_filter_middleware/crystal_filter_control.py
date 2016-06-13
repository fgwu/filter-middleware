import crystal_filter_storlet_gateway as storlet_gateway
from swift.common.swob import Request
import json

PACKAGE_NAME = __name__.split('.')[0]


class Singleton(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):  # @NoSelf
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

    
class CrystalFilterControl(object):
    __metaclass__ = Singleton
    
    def __init__(self, conf, log):
        self.logger = log
        self.conf = conf
        self.server = self.conf.get('execution_server')
                
    def _setup_storlet_gateway(self, conf, logger, request_data):
        return storlet_gateway.SDSGatewayStorlet(conf, logger, request_data)
        
    def _load_native_filter(self, filter_data):
        (modulename, classname) = filter_data['main'].rsplit('.', 1)
        m = __import__(PACKAGE_NAME+'.'+modulename, globals(), 
                       locals(), [classname])
        m_class = getattr(m, classname)
        metric_class = m_class(filter_conf = filter_data,
                               global_conf = self.conf, 
                               logger = self.logger)

        return metric_class
            
    def execute_filters(self, req_resp, filter_exec_list, app,
                        api_version, account, container, obj, method):
        
        requets_data = dict()
        requets_data['app'] = app
        requets_data['api_version'] = api_version
        requets_data['account'] = account
        requets_data['container'] = container
        requets_data['object'] = obj
        requets_data['method'] = method
        
        on_other_server = dict()
        filter_executed = False
        storlet_gw = None
        app_iter = None
        
        for key in sorted(filter_exec_list):
            filter_data = filter_exec_list[key]            
            server = filter_data["execution_server"]            
            if server == self.server:
                if filter_data['type'] == 'storlet':
                    if not storlet_gw:
                        storlet_gw = self._setup_storlet_gateway(self.conf, 
                                                                 self.logger, 
                                                                 requets_data)

                    app_iter = storlet_gw.execute_storlet(req_resp,
                                                          filter_data,
                                                          app_iter)
                    filter_executed = True

                else:
                    self.logger.info('Crystal Filters - Go to execute native '
                                     'Filter: '+ filter_data['main'])
                    native_filter = self._load_native_filter(filter_data)
                    app_iter = native_filter.execute(req_resp, app_iter, 
                                                     requets_data)
                    filter_executed = True
                    
            else:
                on_other_server[key] = filter_exec_list[key]
              
        if on_other_server:
            req_resp.headers['CRYSTAL-FILTERS'] = json.dumps(on_other_server)
        
        if filter_executed:
            if isinstance(req_resp, Request):
                req_resp.environ['wsgi.input'] = app_iter
            else:
                req_resp.app_iter = app_iter
           
        return req_resp
