import mimetypes
import os
from odoo import http
from odoo.http import request
from odoo.modules.module import get_module_resource

_ROUTES = {
    '/':          'home.html',
    '/industries':'industries.html',
    '/why-us':    'why-us.html',
    '/pricing':   'pricing.html',
    '/faq':       'faq.html',
    '/contact':   'contact.html',
}


def _file_response(path, content_type=None):
    if not path or not os.path.isfile(path):
        return request.not_found()
    with open(path, 'rb') as f:
        data = f.read()
    if not content_type:
        content_type, _ = mimetypes.guess_type(path)
        content_type = content_type or 'application/octet-stream'
    return request.make_response(data, headers=[('Content-Type', content_type)])


class SpcwWebsite(http.Controller):

    @http.route(list(_ROUTES.keys()), type='http', auth='public', csrf=False, website=False)
    def marketing_page(self, **kw):
        filename = _ROUTES[request.httprequest.path]
        path = get_module_resource('spcw_website', 'static', 'pages', filename)
        return _file_response(path, 'text/html; charset=utf-8')

    @http.route('/assets/<path:subpath>', type='http', auth='public', csrf=False, website=False)
    def react_asset(self, subpath, **kw):
        path = get_module_resource('spcw_website', 'static', 'assets', subpath)
        return _file_response(path)

    @http.route('/favicon.svg', type='http', auth='public', csrf=False, website=False)
    def favicon(self, **kw):
        path = get_module_resource('spcw_website', 'static', 'favicon.svg')
        return _file_response(path)

    @http.route('/icons.svg', type='http', auth='public', csrf=False, website=False)
    def icons(self, **kw):
        path = get_module_resource('spcw_website', 'static', 'icons.svg')
        return _file_response(path)
