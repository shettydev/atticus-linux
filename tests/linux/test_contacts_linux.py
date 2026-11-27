import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import linux

SAM = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sam Rivera\r\nNICKNAME:Sammy\r\nORG:Acme\r\nEMAIL;TYPE=WORK:sam@acme.example\r\nTEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n'
MOM = 'BEGIN:VCARD\nVERSION:3.0\nFN:Ana Lopez\nEMAIL:ana@example.org\nEND:VCARD\n'


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        import brain_linux
        self.home = tempfile.mkdtemp()
        Path(self.home, '.local/share/contacts').mkdir(parents=True)
        Path(self.home, '.local/share/contacts/sam.vcf').write_text(SAM)
        book = Path(self.home, '.local/share/evolution/addressbook/system')
        book.mkdir(parents=True)
        db = sqlite3.connect(book / 'contacts.db')
        db.execute('CREATE TABLE folder_id (uid TEXT, vcard TEXT)')
        db.execute('INSERT INTO folder_id VALUES (?, ?)', ('1', MOM))
        db.commit()
        db.close()
        p = mock.patch.object(brain_linux, '_home', lambda: self.home)
        p.start()
        self.addCleanup(p.stop)

    def test_contact_matches_shape_unchanged(self):
        out = json.loads(self.a.contacts('sam'))
        self.assertEqual(set(out), {'matches', 'ownCard', 'profile', 'guidance'})
        self.assertEqual(out['matches'], [{'name': 'Sam Rivera', 'emails': ['sam@acme.example'], 'phones': ['+1 555 0100']}])

    def test_evolution_address_book_read(self):
        out = json.loads(self.a.contacts('ana'))
        self.assertEqual(out['matches'][0]['emails'], ['ana@example.org'])

    def test_nickname_and_company_match(self):
        self.assertEqual(len(json.loads(self.a.contacts('sammy'))['matches']), 1)
        self.assertEqual(len(json.loads(self.a.contacts('acme'))['matches']), 1)

    def test_gecos_name_parsed(self):
        import brain_linux
        with mock.patch.object(brain_linux, '_gecos', return_value='Prathik Example,,,'):
            p = self.a.mac_profile()
        self.assertEqual(p['contacts']['name'], 'Prathik Example')
        self.assertEqual(p['errors'], ['mail'])


if __name__ == '__main__':
    unittest.main()
