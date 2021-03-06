from swift.proxy.controllers.base import get_account_info
from swift.common.utils import config_true_value
import redis


class NotCrystalRequest(Exception):
    pass


def _request_instance_property():
    """
    Set and retrieve the request instance.
    This works to force to tie the consistency between the request path and
    self.vars (i.e. api_version, account, container, obj) even if unexpectedly
    (separately) assigned.
    """

    def getter(self):
        return self._request

    def setter(self, request):
        self._request = request
        try:
            self._extract_vaco()
        except ValueError:
            raise NotCrystalRequest()

    return property(getter, setter,
                    doc="Force to tie the request to acc/con/obj vars")


class CrystalBaseHandler(object):
    """
    This is an abstract handler for Proxy/Object Server middleware
    """
    request = _request_instance_property()

    def __init__(self, request, conf, app, logger, filter_control):
        """
        :param request: swob.Request instance
        :param conf: gateway conf dict
        """
        self.request = request
        self.server = conf.get('execution_server')
        self.sds_containers = [conf.get('storlet_container'),
                               conf.get('storlet_dependency'),
                               conf.get('storlet_images', 'docker_images')]
        self.app = app
        self.logger = logger
        self.conf = conf
        self.filter_control = filter_control

        self.redis_host = conf.get('redis_host')
        self.redis_port = conf.get('redis_port')
        self.redis_db = conf.get('redis_db')
        self.cache = conf.get('cache')

        self.method = self.request.method.lower()

        self.redis = redis.StrictRedis(self.redis_host,
                                       self.redis_port,
                                       self.redis_db)

    def _extract_vaco(self):
        """
        Set version, account, container, obj vars from self._parse_vaco result
        :raises ValueError: if self._parse_vaco raises ValueError while
                            parsing, this method doesn't care and raise it to
                            upper caller.
        """
        self._api_version, self._account, self._container, self._obj = \
            self._parse_vaco()

    @property
    def api_version(self):
        return self._api_version

    @property
    def account(self):
        return self._account

    @property
    def container(self):
        return self._container

    @property
    def obj(self):
        return self._obj

    @property
    def is_crystal_valid_request(self):
        if self.server == 'proxy':
            storlet_enabled = self.is_account_storlet_enabled()
        else:
            storlet_enabled = True
        crystal_container = self.container in self.sds_containers

        return (not crystal_container and self.obj and storlet_enabled)

    def _parse_vaco(self):
        """
        Parse method of path from self.request which depends on child class
        (Proxy or Object)
        :return tuple: a string tuple of (version, account, container, object)
        """
        raise NotImplementedError()

    def handle_request(self):
        """
        Run storlet
        """
        raise NotImplementedError()

    @property
    def is_storlet_execution(self):
        return 'X-Run-Storlet' in self.request.headers

    @property
    def is_range_request(self):
        """
        Determines whether the request is a byte-range request
        """
        return 'Range' in self.request.headers

    def is_available_trigger(self):
        return any((True for x in self.available_triggers
                    if x in self.request.headers.keys()))

    @property
    def is_slo_get_request(self):
        """
        Determines from a GET request and its  associated response
        if the object is a SLO
        """
        return self.request.params.get('multipart-manifest') == 'get'

    def is_slo_response(self, resp):
        self.logger.debug(
            'Verify if {0}/{1}/{2} is an SLO assembly object'.format(
                self.account, self.container, self.obj))
        is_slo = 'X-Static-Large-Object' in resp.headers
        if is_slo:
            self.logger.debug(
                '{0}/{1}/{2} is indeed an SLO assembly '
                'object'.format(self.account, self.container, self.obj))
        else:
            self.logger.debug(
                '{0}/{1}/{2} is NOT an SLO assembly object'.format(
                    self.account, self.container, self.obj))
        return is_slo

    def is_account_storlet_enabled(self):
        account_meta = get_account_info(self.request.environ,
                                        self.app)['meta']
        storlets_enabled = account_meta.get('storlet-enabled',
                                            'False')

        if not config_true_value(storlets_enabled):
            return False

        return True

    def _call_filter_control_on_put(self, filter_list):
        """
        Call gateway module to get result of filter execution
        in PUT flow
        """
        return self.filter_control.execute_filters(self.request, filter_list,
                                                   self.app, self._api_version,
                                                   self.account, self.container,
                                                   self.obj, self.method)

    def _call_filter_control_on_get(self, req_resp, filter_list):
        """
        Call gateway module to get result of filter execution
        in GET flow
        """
        return self.filter_control.execute_filters(req_resp, filter_list,
                                                   self.app, self._api_version,
                                                   self.account, self.container,
                                                   self.obj, self.method)

    def apply_filters_on_pre_get(self, filter_list):
        filtered_filter_list = dict()
        for key, filter_data in filter_list.items():
            if filter_data['when'] == 'on_pre_get':
                filtered_filter_list[key] = filter_data

        if filtered_filter_list:
            self.logger.info('Crystal Filters - Go to execute filters on PRE-GET: ' + str(filtered_filter_list))
            self._call_filter_control_on_get(self.request, filtered_filter_list)

    def apply_filters_on_post_get(self, resp, filter_list):
        filtered_filter_list = dict()
        for key, filter_data in filter_list.items():
            if filter_data['when'] == 'on_post_get':
                filtered_filter_list[key] = filter_data

        if filtered_filter_list:
            self.logger.info('Crystal Filters - Go to execute filters on POST-GET: ' + str(filtered_filter_list))
            resp = self._call_filter_control_on_get(resp, filtered_filter_list)

        return resp

    def apply_filters_on_pre_put(self, filter_list):
        filtered_filter_list = dict()
        for key, filter_data in filter_list.items():
            if filter_data['when'] == 'on_pre_put':
                filtered_filter_list[key] = filter_data

        if filtered_filter_list:
            self.logger.info('Crystal Filters - Go to execute filters on PRE-PUT: ' + str(filtered_filter_list))
            self.request = self._call_filter_control_on_put(filtered_filter_list)

            if 'CONTENT_LENGTH' in self.request.environ:
                self.request.environ.pop('CONTENT_LENGTH')
            self.request.headers['Transfer-Encoding'] = 'chunked'
