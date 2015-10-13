#!/usr/bin/env python
import requests, json, sys, time, re
from pprint import pprint
import autofocus_config

AF_APIKEY = autofocus_config.AF_APIKEY

# Useful information:
#
# * We're not doing any input validation in the client itself. We pass
#   the data to the API, and rely on 4XX errors to communicate invalid
#   requests. At the time this was written, the API DOES NOT validate search
#   values. So you can offer invalid IPs, such as 592.99.1.1 and it will
#   not balk. The result set will be empty, naturally.

_base_url = "https://autofocus.paloaltonetworks.com/api/v0.9"
_headers = {"Content-Type" : "application/json"}

class NotLoaded(object):
    pass

class AFClientError(Exception):
    def __init__(self, message, resp = None):
        super(AFClientError, self).__init__(self, message)
        self.resp = resp
        self.message = message

class AFServerError(Exception):
    def __init__(self, message, resp):
        super(AFServerError, self).__init__(self, message)
        self.resp = resp
        self.message = message


class AutoFocusAPI(object):

    page_size = 50
    search_operator = "all"

    def __init__(self, **kwargs):
        for k,v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return self.__dict__.__str__()
    
    def __str__(self):
        return self.__dict__.__str__()

    @classmethod
    def _api_request(cls, path, post_data = {}, params = {}):

        if not AF_APIKEY:
            raise Exception("AF_APIKEY is not set. Library requires an APIKEY to be set")
            
        post_data["apiKey"] = AF_APIKEY
        
        resp = requests.post(_base_url + path, params = params, headers=_headers, data=json.dumps(post_data))

        if resp.status_code >= 400 and resp.status_code < 500:
            raise AFClientError(resp._content, resp)
        
        if resp.status_code >= 500 and resp.status_code < 600:
            raise AFServerError(resp._content, resp)

        return resp

    @classmethod
    def _api_search_request(cls, path, post_data):

        post_data["size"]   = cls.page_size
        post_data["from"]   = 0
        post_data["sort"]   = {
            "create_date": {
                "order": "desc"
            }
        }
        post_data['scope'] = "Global"

        while True:

            # There is currently a hardcoded limitation for 4000 samples per query.
            # Hardcode StopIteration until that issue is addressed
            if post_data['from'] >= 4000:
                raise StopIteration()

            if post_data['from'] + post_data['size'] > 4000:
                post_data['size'] = 4000 - post_data['from']

            # TODO: Remove the above logic once the result cap is removed

            init_query_time = time.time()
            init_query_resp = cls._api_request(path, post_data = post_data)
            init_query_data = init_query_resp.json()
            post_data['from'] += post_data['size']
            af_cookie = init_query_data['af_cookie']

            resp_data = {}
            prev_resp_data = {}
            i = 0
            while True:

                i += 1

                request_url = "/" + path.split("/")[1] + "/results/" + af_cookie

                # Try our request. Check for AF Cookie going away, which is weird
                # Catch it and add more context. Then throw it up again
                try:
                    resp = cls._api_request(request_url)
                    resp_data = resp.json()
                except AFClientError as e:
                    # TODO: Can be removed once AF Cookie going away bug is fixed.
                    if "AF Cookie Not Found" in e.message:
                        raise AFClientError("Auto Focus Cookie has gone away after %d queries taking %f seconds. Server said percent complete was at %f, last query." \
                                        % (i, time.time() - init_query_time, prev_resp_data['af_complete_percentage']), e.resp)
                    else:
                        raise e

                # If we've gotten our bucket size worth of data, or the query has complete
                if len(resp_data.get('hits', [])) == post_data['size'] \
                        or resp_data.get('af_complete_percentage', 100) == 100:
                    break

                prev_resp_data = resp_data

                continue

            if not resp_data.get('hits', None):
                raise StopIteration()

            yield resp_data

    @classmethod
    def _api_search(cls, *args, **kwargs):

        args = list(args)

        # Classes that inherit from AutoFocusAPI need to pass a search path as the 
        # first arg to the protected _api_search method
        path = args.pop(0)

        if len(args) == 1:
            post_data = {
                "query" : json.loads(args[0])
            }
        else:
            # Just do an or here, we'll do validation below
            if "field" in kwargs or "value" in kwargs:
                args.append(kwargs)
                
            post_data = {
                "query": {
                    "operator": cls.search_operator,
                    "children": []
                }
            }

            for kwarg in args:        
     
                # Check and make sure field and value are passed to search - req'd
                for arg in ('field', 'value'):
                    if arg not in kwarg:
                        raise Exception

                # Build the searching paramaters to be passed to the _api_request method
                # _api_request will add the additional data needed to form a valid request
                post_data['query']['children'].append({
                    "field": kwarg['field'],
                    "operator": kwarg.get('operator', "is"),
                    "value": kwarg['value']
                })

        for res in cls._api_search_request(path, post_data = post_data):
            for hit in res['hits']:
                yield hit

class AFTag(AutoFocusAPI):

    def __init__(self, **kwargs):

        self.comments = kwargs.get("comments", NotLoaded())
        self.refs = kwargs.get("refs", NotLoaded())
        self.review = kwargs.get("review", NotLoaded())
        self.support_id = kwargs.get("support_id", NotLoaded())

        for k,v in kwargs.items():
            setattr(self, k, v)

    def __getattribute__(self, attr):

        value = object.__getattribute__(self, attr)

        # Not offered in the list controller, have to call get to lazyload:
        #      comments, refs, review, support_id
        if attr in ('comments', 'refs', 'review', 'support_id') and type(value) is NotLoaded:

            # Reloading the data via the get method
            self = AFTag.get(self.public_tag_name)
            value = object.__getattribute__(self, attr)

        return value

    @classmethod
    def list(cls, *args, **kwargs):

        kwargs['scope'] = kwargs.get("scope", "Visible")
        kwargs['sortBy'] = kwargs.get("sortBy", "name")
        kwargs['order'] = kwargs.get("order", "asc")
        kwargs['pageSize'] = kwargs.get("pageSize", 1000)
        kwargs['pageNum'] = kwargs.get("pageNum", 0)
                              
        resp = cls._api_request("/tags/", params = kwargs).json()
        results = []

        for tag in resp['tags']:
            results.append(AFTag(**tag))            

        return results
    
    @classmethod
    def get(cls, tag_name):

        # TODO: This can probably use more validation

        resp = cls._api_request("/tag/" + tag_name).json()

        return AFTag(**resp['tag'])            

class AFSession(AutoFocusAPI):

    @classmethod
    def search(cls, *args, **kwargs):

        for res in cls._api_search("/sessions/search", *args, **kwargs):
            yield AFSession(**res['_source'])

class AFSample(AutoFocusAPI):

    def get_analyses(self, sections = ["file"], platforms = ["win7", "winxp"]):

        resp = self.__class__._api_request("/sample/" + self.sha256 + "/analysis", \
                    post_data = { "sections" : sections, "platforms" : platforms }).json()

        return resp
                              
    @classmethod
    def search(cls, *args, **kwargs):

        for res in cls._api_search("/samples/search", *args, **kwargs):
            yield AFSample(**res['_source'])

    @classmethod
    def get(cls, hash):

        if not re.match(r'^([A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})$', hash):
            raise KeyError("Argument mush be a valid md5, sha1, or sha256 hash")

        res = None

        try:
            if len(hash) == 32:
                res = cls.search(field = "sample.md5", value = hash).next()
            elif len(hash) == 40:
                res = cls.search(field = "sample.sha1", value = hash).next()
            elif len(hash) == 64:
                res = cls.search(field = "sample.sha256", value = hash).next()
        except StopIteration:
            pass

        if not res:
            raise KeyError("No such hash found in AutoFocus")

        return res

if __name__ == "__main__":

#    i = 0
#    for sample in AFSample.search(field = "sample.malware", value = "1", operator = "is"):
#        i += 1
#        pprint(sample.__dict__)
#        pprint(sample.get_analyses())
#    print "%d results" % (i,)

    # Get a sample by hash
    sample = AFSample.get("585fa6e62424037461b8cb9e6b59597e54f2b74510b1efea2a14be4f58bae4eb")
    print sample.md5

    sample = AFSample.get("7c49955374b0b8105d6ca34dafeb3769")
    print sample.sha1

    sample = AFSample.get("bd3ccbddd8e3da2f4de04974e744e2a776539cf5")
    print sample.sha256
