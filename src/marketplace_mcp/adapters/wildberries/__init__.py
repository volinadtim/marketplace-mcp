"""wildberries.ru, over the JSON APIs its own account pages call.

Unlike Ozon, almost nothing here needs a browser. WB authorises its APIs with a
bearer token that its sign-in SDK leaves in the page's local storage, and the
token is good for thirty days — so the browser is opened once to read it and
everything after that is plain HTTP.
"""
