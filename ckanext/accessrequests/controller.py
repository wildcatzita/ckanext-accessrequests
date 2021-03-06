from ckan.common import c, response, request, g
from ckan.controllers.user import UserController
import binascii
import ckan.authz as authz
import ckan.model as model
import ckan.lib.base as base
import ckan.lib.helpers as h
import ckan.lib.mailer as mailer
import ckan.logic as logic
import logging
import os
import random
from pylons import config
from ckan.logic.validators import (object_id_validators, user_id_exists)

#from model import UserTitle

log = logging.getLogger(__name__)

abort = base.abort
render = base.render
_ = base._

NotFound = logic.NotFound
NotAuthorized = logic.NotAuthorized
ValidationError = logic.ValidationError


def all_account_requests():
    '''Return a list of all pending user accounts
    '''
    # TODO: stop this returning invited users also
    return model.Session.query(model.User).filter(model.User.state=='pending').all()

class AccessRequestsController(UserController):

    def request_account(self, data=None, errors=None, error_summary=None):
        '''GET to display a form for requesting a user account or POST the
           form data to submit the request.
        '''
        context = {
            'model': model,
            'session': model.Session,
            'user': c.user or c.author,
            # why?
            #'user': model.Session.query(model.User).filter_by(sysadmin=True).first().name,
            'auth_user_obj': c.userobj,
            'schema': self._new_form_to_db_schema(),
            'save': 'save' in request.params
        }
        if context['save'] and not data:
            return self._save_new_pending(context)

        if c.user and not data:
            # Don't offer the registration form if already logged in
            return render('user/logout_first.html')
        
        data = data or {}
        errors = errors or {}
        error_summary = error_summary or {}
        organizations = logic.get_action('organization_list')({}, {})
        organization = []
        for org in organizations:
          organization.append(logic.get_action('organization_show')({},{'id': org}))

        vars = {'data': data, 'errors': errors, 'error_summary': error_summary, 'organization': organization}

        c.is_sysadmin = authz.is_sysadmin(c.user)
        c.form = render(self.new_user_form, extra_vars=vars)
        return render('user/new.html')

    def _save_new_pending(self, context):
        params = request.params
        password = str(binascii.b2a_hex(os.urandom(15)))
        data = dict(
            fullname = params['fullname'],
            name = params['name'],
            password1 = password,
            password2 = password,
            state = model.State.PENDING,
            email = params['email'],
            organization_request = params['organization-for-request'],
            reason_to_access = params['reason-to-access']
            )
        organization = model.Group.get(data['organization_request'])
        try:
            user_dict = logic.get_action('user_create')(context, data)
            context1 = { 'user': model.Session.query(model.User).filter_by(sysadmin=True).first().name }
            msg = "Dear Admin,\n\nA request for a new user account has been submitted:\nUsername: " + data['name'] + "\nName: " + data['fullname'] + "\nEmail: " + data['email'] + "\nOrganisation: " + organization.display_name + "\nReason for access: " + data['reason_to_access'] + "\n\nThis request can be approved or rejected at " + g.site_url + h.url_for(controller='ckanext.accessrequests.controller:AccessRequestsController', action='account_requests')
            mailer.mail_recipient('Admin', config.get('ckanext.accessrequests.approver_email'), 'Account request', msg)
            h.flash_success('Your request for access to the {0} has been submitted.'.format(config.get('ckan.site_title')))
        except ValidationError, e:
            # return validation failures to the form
            errors = e.error_dict
            error_summary = e.error_summary
            return self.request_account(data, errors, error_summary)

        # TODO: turn into a template
        # msg = "New account's request:\nUsername: {name}\nEmail: {email}\nAgency: {agency}\nRole: {role}\nNotes: {notes}".format(**params)
 
        # redirect to confirmation page/display success flash message
        h.redirect_to('/')
        
    def account_requests(self):
        ''' /ckan-admin/account_requests rendering
        '''
        context = {'model': model,
                   'user': c.user, 'auth_user_obj': c.userobj}
        orgs = logic.get_action('organization_list_for_user')({'user': c.user}, {'permission': 'admin'})
        user_is_admin_in_top_org = None
        if orgs:
            for org in orgs:
                group = model.Group.get(org['id'])
                if group.id == (group.get_parent_group_hierarchy(type='organization') or [group])[0].id:
                    user_is_admin_in_top_org = True
                    break
        try:
            user_is_admin_in_top_org or logic.check_access('sysadmin', context, {})
        except NotAuthorized:
            base.abort(401, _('Need to be system administrator or admin in top-level org to administer'))
        accounts = [{
            'id':user.id,
            'name':user.display_name,
            'username': user.name,
            'email': user.email,
        } for user in all_account_requests()]
        return render('admin/account_requests.html', {'accounts': accounts})

    def account_requests_management(self):
        ''' Approve or reject an account request
        '''
        action = request.params['action']
        user_id = request.params['id']
        user_name = request.params['name']
        user = model.User.get(user_id)
        #user_email = logic.get_action('user_show')({},{'id': user_id})
        context1 = { 'user': model.Session.query(model.User).filter_by(sysadmin=True).first().name }
        org = logic.get_action('organization_list_for_user')({'user': user_name}, {'permission': 'read'})

        if org:
            user_delete = {
                'id': org[0]['name'],
                'object': user_name,
                'object_type': 'user'
            }

        context = {
            'model': model,
            'user': c.user,
            'session': model.Session,
        }
        activity_create_context = {
            'model': model,
            'user': user_name,
            'defer_commit': True,
            'ignore_auth': True,
            'session': model.Session
        }
        activity_dict = {
            'user_id': c.userobj.id,
            'object_id': user_id
        } 
        if action == 'forbid':
            object_id_validators['reject new user'] = user_id_exists
            activity_dict['activity_type'] = 'reject new user'
            logic.get_action('activity_create')(activity_create_context, activity_dict)
            # remove user, {{'user_email': user_email}}

            logic.get_action('user_delete')(context1, {'id':user_id})

            mailer.mail_recipient(user.name, user.email, 'Account request', 'Your account request has been denied.')

        elif action == 'approve':
            object_id_validators['approve new user'] = user_id_exists
            activity_dict['activity_type'] = 'approve new user'
            logic.get_action('activity_create')(activity_create_context, activity_dict)
            # Send invitation to complete registration
            try:
                mailer.send_invite(user)
            except Exception as e:
                log.error('Error emailing invite to user: %s', e)
                abort(500, _('Error: couldn''t email invite to user'))

        response.status = 200
        return render('admin/account_requests_management.html')