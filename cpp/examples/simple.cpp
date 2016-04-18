//
// Very simple example of how to use RCDB API from C++
//

#include <string>
#include <iostream>

#include "RCDB/Connection.h"


int main ( int argc, char *argv[] )
{
    using namespace std;

    // Get a connection string from arguments
    if ( argc != 2 ) {
        cout<<"usage: "<< argv[0] <<" <connection_string>\n";
        return 1;
    }
    string connection_str(argv[1]);


    // Create DB connection
    rcdb::Connection connection(connection_str);

    // Get condition with name int_cnd for run 1.
    // Change it to event_count and run for 10452 if you connect to a real database
    auto cnd = connection.GetCondition(1, "int_cnd");

    //cnd will be null if no such condition saved for the run
    if(!cnd)
    {
        cout<<"The condition is not found for the run"<<endl;
        return 1;
    }

    // get the value!
    int value = cnd->ToInt();
    cout<<"The condition value is: "<< value << endl;
    return 0;
}