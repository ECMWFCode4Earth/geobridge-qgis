API Key Tab
============

.. figure:: _static/screenshots/api_key_tab.png
   :alt: The API Key tab
   :width: 500px

The first tab handles two things: getting the ``geobridge`` library
installed, and authenticating against the Copernicus Climate Data Store.

Dependency status
------------------

At the top, a button shows whether ``geobridge`` (and its export-support
extras) are installed into QGIS's own Python:

* **"Install dependencies"** — not installed yet, or only partially
  installed. Clicking it installs everything needed.
* **"✓ geobridge installed (version)"** — fully installed and ready; the
  button is disabled.

See :doc:`installation` for what exactly gets installed and why.

API key
--------

* **Copernicus CDS API key** — paste your personal access token from your
  CDS profile page here. The field is masked by default; click the eye
  icon inside it to reveal or re-hide what you typed.
* **Save** — stores the key (via QGIS's ``QSettings``) and immediately
  attempts to authenticate with it. The status label next to the button
  reports the result ("Authenticated." or an error message).

Search and quick WMTS previews (:doc:`search_tab`, :doc:`browse_tab`) work
without a saved key. An authenticated key is only required for:

* GeoTIFF export (both the Search tab's ARCO Zarr export and the Browse
  tab's CDS API download).
* "Full history" time series on the :doc:`time_series_tab`.

Getting a CDS API key
-----------------------

If you don't already have a Copernicus CDS account and key, here's the
full path from a blank browser tab to a working one.

1. Go to `cds.climate.copernicus.eu <https://cds.climate.copernicus.eu>`_
   and click **Login - Register** in the top right.

   .. figure:: _static/screenshots/cds_login.png
      :alt: The Climate Data Store homepage with Login - Register highlighted
      :width: 450px

2. On the login page, click **Register new user** — CDS accounts are
   managed through ECMWF's own login system, not a separate CDS-specific
   one.

   .. figure:: _static/screenshots/cds_register.png
      :alt: The ECMWF login page with Register new user highlighted
      :width: 450px

3. Fill in your name, email, and a password, then click **Register**. A
   confirmation email follows — you need to click the link in it before
   the account is active.

   .. figure:: _static/screenshots/cds_fill_details.png
      :alt: The ECMWF new-account registration form
      :width: 450px

4. Log in, open **Your profile**, and switch to the **Licences** tab.
   Accept the Terms of use of the Copernicus Climate Data Store (and the
   data protection statement) — CDS won't serve any data to your account
   until these are accepted, and a download will fail with a licence
   error otherwise.

   .. figure:: _static/screenshots/cds_accept_licences.png
      :alt: The Licences tab on the CDS profile page, every item accepted
      :width: 450px

5. Still on **Your profile** (pictured below is the top of that page):

   .. figure:: _static/screenshots/cds_profile.png
      :alt: The Your profile page on the Climate Data Store
      :width: 450px

   Scroll down past your personal details to the **API key** box further
   down the same page, and click the copy icon next to the masked key
   (the two overlapping squares, to the right of the refresh icon). That
   copied value is what goes into the **Copernicus CDS API key** field
   above.

   .. figure:: _static/screenshots/cds_api_key_copy.png
      :alt: The API key box on the CDS profile page, with the copy icon highlighted
      :width: 450px

.. note::
   The token belongs to your CDS account, not to this plugin — the same
   value also works with the standalone ``cdsapi`` Python package or a
   manual ``~/.cdsapirc`` file, if you use those elsewhere too.

Two things trip people up on a first attempt
-------------------------------------------------

* **The licence has to be accepted, not just the account created.**
  Registering an account and accepting the licence (step 4 above) are
  two separate things — a freshly registered account authenticates
  fine, but CDS still rejects every download with a licence error until
  the Terms of use are explicitly accepted on the **Licences** tab.

  .. figure:: _static/screenshots/cds_accept_licences.png
     :alt: The Licences tab on the CDS profile page, every item accepted
     :width: 450px

* **Paste only the key itself.** The copy icon (step 5 above) copies
  just the raw token — that's the only part that goes into this tab's
  **Copernicus CDS API key** field below. Don't paste the ``url:`` line
  or the ``key:`` label CDS shows next to it in the ``.cdsapirc``
  snippet — those are for the separate standalone ``cdsapi`` Python
  package, not for this plugin.

  .. figure:: _static/screenshots/api_key_tab.png
     :alt: The API Key tab, with the Copernicus CDS API key field to paste the token into
     :width: 500px
